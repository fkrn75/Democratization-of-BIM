# S11 보고서: 양쪽 클라이언트 종단 시험 (Codex·Claude 측정 완료, 커맨드 125개 덮음: 54개는 8절, 새 71개는 10절)

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

## 7. 통합 코어(54개) 재측정: Codex npm 0.147.0 (spike/s11-claude)

근거: `results/s11_layer_c_codex_npm_54.json`, `results/s11_explore_codex_npm_54.json`. 하네스는 `tools/layer_c.py`에 그룹 G6(뷰·주석·도면 내보내기 13개), G7(벽 분할·가져오기·병합 4개), G8(패밀리 6개)을 추가해 54개 전부를 덮도록 확장했다. 코어는 origin/main c010f60.

**Claude 쪽은 로그인 대기로 미측정이다.** `claude -p`가 "Not logged in"으로 실패해 Claude Code CLI 층 C·탐색·이미지는 시험하지 못했다. 로그인 후 같은 하네스(`python layer_c.py claude <out.json>`, `python explore.py claude <out.json>`)로 재개한다.

| 항목 | Codex 0.147 결과 | 판정 |
|---|---|---|
| 패리티 A(층 C) | 54개 중 54개 호출 통과, 성공 호출 54개. G1~G8 종료 코드 모두 0, 작업 폴더 밖 파일 0 | 통과 |
| G4 오류 코드 | 9개 중 7개 기대 코드 일치(PATH_DENIED, UNKNOWN_COMMAND, SCHEMA_INVALID, RISK_MISMATCH, NOT_FOUND, job NOT_FOUND, render UNSUPPORTED). 2개 불일치는 아래 원인 참조 | 서버 동작은 정상 |
| G5 이어받기 | history.affected 9쪽, 250개 id 전부 수집, 잘림 표시와 커서 사용 확인 | 통과 |
| 탐색 20개 | 19/20 성공(95%), 평균 검색 호출 8.95(성공만 9.32), 평균 도구 호출 12.6. 실패 T14(IFC 내보내기)는 확인 토큰 단계에서 CONFIRM_REQUIRED로 멈춤 | 통과(기준 90%) |
| 이미지 | `dobim_render`가 UNSUPPORTED(미구현). 시트 이미지는 아직 없어 판정 불가 | 미구현 |
| 크기 | 도구 응답 최대 5,959B(바이트/3 추정 약 2,000토큰), 8,000토큰 이내, 잘림 오류 0 | 통과 |
| 시간 | initialize 1.4~2.0초, 도구 응답 중앙값 2~27ms, 최대 3.6초(30초 이내). 서버 stdout 비JSON 줄 0 | 통과 |
| 결과 동등(RA-01·roundtrip.check) | 미시험(54개 기준 시나리오 스크립트 없음) | 미시험 |
| 안내문 생성물 검사 | 이 저장소에 해당 검사 도구 없음 | 미구현 |
| 분기 금지 린트·Windows 점검 | 31개 기준 결과(5절)만 있고 재측정 안 함 | 미재측정 |

**G4 불일치 2건(e_rev_conflict, f_dry_run_ok) 원인: 모델 호출 방식이다.** 원시 기록상 모델이 과제 문구의 이름 `W-000001`을 id로 그대로 넘겼고, 서버는 id 형식 위반으로 SCHEMA_INVALID를 냈다. expected_rev·dry_run 검사 경로까지 가지 못했다. 서버 결함이 아니라 시험 과제(이름 사용)와 모델의 이름→id 해석 부족이다. 같은 이름으로 호출한 `c`(dx 'abc')는 우연히 같은 코드라 일치로 보였다. 개선은 하네스 G4 문구에 실제 id를 주거나 서버가 이름 해석을 안내하는 쪽이다.

토큰(Codex 보고값, 입력+출력, 캐시 포함): 층 C 약 415만, 탐색 약 608만. Claude 값은 없다.

## 8. Claude Code CLI 2.1.325 측정 (Sonnet 5.5, spike/s11-claude-run)

근거: `results/s11_layer_c_claude_54.json`, `results/s11_explore_claude_54.json`. 코어는 origin/main aef8de2. 7절의 "Claude 쪽 미측정"을 대체한다. 구독 로그인으로 `claude -p`가 성공했고, 하네스 실행 규칙은 아래와 같다.

- 깨끗한 환경: 하위 프로세스 env를 PATH, HOME, USERPROFILE, APPDATA, LOCALAPPDATA, SYSTEMROOT, SystemRoot, TEMP, TMP, USERNAME 만 남긴다(`common.claude_clean_env`). 데스크톱 앱이 물려준 ANTHROPIC_*·CLAUDE_* 변수가 있으면 인증이 실패한다.
- 컨텍스트 최소화: `--strict-mcp-config --mcp-config`(dobim 서버만), `--tools ""`, `--setting-sources project`, `--disable-slash-commands`, `--exclude-dynamic-system-prompt-sections`, `--no-session-persistence`, `--model sonnet`. 작업 폴더는 사용자 홈 밖에 둬 상위 폴더 지침 파일이 로드되지 않게 했다.
- 하네스 변경: `S11_PY` 환경 변수로 코어 venv 파이썬을 지정할 수 있게 했다(워크트리에 .venv가 없을 때).

| 항목 | Claude Code 2.1.325 | Codex npm 0.147.0 | 판정 |
|---|---|---|---|
| 패리티 A(층 C) | 54개 중 54개 호출 통과, 성공 호출 54개. G1~G8 종료 코드 모두 0, 작업 폴더 밖 파일 0 | 54/54 | 통과 |
| G4 오류 코드 | 9개 중 9개 기대 코드 일치. wall.move 호출 5건 모두 이름이 아니라 22자리 id를 넘김(이름→id 조회를 먼저 수행) | 7/9(이름을 id로 넘겨 SCHEMA_INVALID) | Claude 통과 |
| G5 이어받기 | history.affected 4쪽, 250개 id 전부 수집, 잘림 표시·커서 3쪽 사용 | 9쪽, 250개 | 통과 |
| 탐색 20개 | 20/20 성공(100%), 평균 검색 호출 4.0, 평균 도구 호출 7.25. T14(IFC 내보내기)도 확인 토큰까지 끝냄 | 19/20, 검색 8.95, 도구 12.6 | 통과(기준 90%) |
| 이미지 | `dobim_render` UNSUPPORTED(G4 i 항목에서 정의된 오류로 확인). 시트 이미지 판정 불가 | 같음 | 미구현 |
| 크기 | 도구 응답 최대 3,334B(G5 history.affected 한 쪽만 전송 바이트 11,828B, 약 4,000토큰 추정). 잘림 오류 0 | 최대 5,959B | 통과 |
| 시간 | 서버 첫 출력 약 1.3초, 도구 응답 중앙값 2.6~8ms, 최대 약 1.2초(dobim_project). 비JSON 줄 0. initialize 시간은 하네스 Claude 경로에서 기록되지 않음(null) | initialize 1.4~2.0초 | 통과 |
| 결과 동등 | 미시험(54개 기준 시나리오 스크립트 없음). 탐색 20개의 최종 모델 상태는 CLI 판정으로 20/20 | 미시험 | 미시험 |

토큰(입력+캐시 생성+캐시 읽기+출력, claude result 사용량): 층 C 약 87.2만(그룹당 4.2만~16.3만), 탐색 20개 약 96.2만(과제당 2.6만~8.8만). 합계 약 183만으로 Codex(층 C 약 415만, 탐색 약 608만)의 약 18%다. 탐색은 10개를 먼저 돌려 토큰을 잰 뒤 나머지 10개를 이어 돌렸고 축소는 없었다. 판정은 도구 호출·서버 응답 봉투 기준이다.

## 9. C-1 잔여 (phase1/c1-residual)

근거: `results/s11_c1_claude_handshake.json`, `results/s11_c1_ra01_equiv.json`, `results/s11_lint_no_client_branch.json`. 코어는 origin/main a6639e6. 8절 표에서 비어 있던 항목 중 LLM 재실행 없이 닫을 수 있는 3건을 메웠다.

| 항목 | 결과 | 판정 |
|---|---|---|
| Claude initialize 시간 null | 원인: Claude Code 2.1.325는 협상 프로토콜 2026-07-28(무상태)을 써서 `initialize`를 보내지 않고 `server/discover`로 시작한다. 하네스 `common.parse_tap`이 `initialize`만 찾아 협상 버전·클라이언트 정보·시간이 모두 null이었다. 수정: `server/discover`도 핸드셰이크로 인식(버전·클라이언트 정보는 params._meta, 응답은 supportedVersions). 재현은 `claude -p` 최소 1회(도구 1회 호출, 깨끗한 환경)의 중계기 기록으로 했다: 핸드셰이크 2.47초(서버 시작 포함), 협상 2026-07-28, 비JSON 줄 0. Codex 기록으로 기존 `initialize` 경로가 그대로 동작함을 확인했다 | 해결 |
| 결과 동등(RA-01 결정적 시나리오) | `tools/ra01_equiv.py`: s10의 RA-01 스크립트(레벨 2·벽 14·문 5, 일괄 76커맨드) 포함 11단계를 CLI 직접 호출과 MCP 경유(공식 SDK 클라이언트, LLM 없음)로 실행. 단계별 결과 봉투 불일치 0, 최종 model.hash(state·derived·ifc·rev 76) 동일, 내보낸 IFC 의미 비교 차이 0(요소 28개), 작업 폴더 밖 쓰기 거부 동일 | 통과 |
| 분기 금지 린트 | 현재 main 코어 소스 23개 파일(8절 당시 15개)에서 클라이언트별 분기 0건, 음성 대조(어휘·구조) 모두 탐지. 주석·문서 문자열 언급 2건(dispatch.py, limits.py의 "Claude")은 분기 아님 | 통과 |

동등 시험에서 비교에서 가린 전송 경로 차이: project.new의 handle(MCP 세션만 발급), history.list의 actor(cli/mcp), 확인 토큰, ifc.export의 bytes. 관찰: 내보낸 IFC의 DATA 구역 바이트는 직접 호출 2회끼리는 같지만 MCP 경유와는 다르다(속성 엔티티 번호 순서와 헤더 시각. 코어 정규 해시·의미 비교는 같음). 바이트 동일성을 합격 기준으로 삼지 않는 한 문제는 아니나 기록해 둔다. 직접 호출 쪽은 CLI의 `script --atomic`, MCP 쪽은 `dobim_execute` 일괄이라 같은 원자 일괄 경로를 비교한 것이다.

**남음(이번 범위 밖)**
- 이미지 판정: `dobim_render`가 UNSUPPORTED(미구현)이고 시트 이미지 산출물이 아직 없어 이미지를 보는 판정 대상이 없다. 도면 시트 작업이 병합된 뒤 한다.
- Windows 54개 LLM 재측정: 커맨드 54개 기준으로 Claude·Codex를 다시 돌려야 해 사용량이 크다(8절 합계 약 183만 토큰). 사용량 절약 지침에 따라 이번엔 하지 않았다. 8절의 Claude 54개 측정이 같은 PC·같은 코어 계열이라 값은 있으나, Windows 점검 항목(시작 시간·한글 경로 등) 54개 기준 재측정은 남는다.
- LLM 경유 결과 동등(Claude·Codex가 RA-01 시나리오를 스스로 수행)은 이번에 LLM을 돌리지 않았으므로 미시험이다. 서버 직접 경로의 동등만 확인했다.

## 10. C-1 마감: 새 커맨드 71개 재측정, 이미지 판정, LLM 경유 RA-01 동등 (phase1/c1-close)

근거: `results/s11_c1close_*.json`. 코어는 origin/main 06d4c0c(커맨드 125개). 하네스는 `tools/layer_c_new.py`(묶음 G9~G14), `tools/explore_tasks_new.py`·`explore_new.py`(탐색 15개), `tools/image_judge.py`(이미지), `tools/ra01_llm.py`(LLM 경유 동등), `tools/layer_c_new_ref.py`(지시문 수행 가능성 점검, LLM 없음)이고, 판정 방식은 8절과 같다(도구 호출·서버 응답 봉투·실행 뒤 CLI 상태 조회, 모델 자기 보고 아님). 사용량 절약 지침에 따라 재측정 범위는 54개 측정 이후 새로 생긴 커맨드 71개로 정했고(사용자 결정), Codex 탐색은 아래 이유로 하지 않았다.

**71개 목록 근거.** 54개 측정 때의 레지스트리(aef8de2)를 `results/s11_c1close_registry_54.json`에 고정하고, 현재 레지스트리 125개에서 그 이름을 뺐다. 삭제된 커맨드 0, 새 커맨드 정확히 71개(쓰기 62·읽기 6·파괴적 3). 묶음별 커맨드 수는 G9 조인·공간·슬래브·벽 층 12, G10 계단·난간·승강기 15, G11 지붕·솔리드·벽 상단/잘라내기 11, G12 대지·통심선·전개 창호 8, G13 2D 작도 16, G14 시트·이미지·높이 치수 9로 중복·누락 없이 71개를 덮는다(`layer_c_new.py selftest`). 54개 시점 이후 메타가 바뀐 기존 커맨드는 `view.export` 1개이며 이번 범위 밖이다.

### 10-1. 층 C: 새 커맨드 71개

| 항목 | Claude Code 2.1.325 (Sonnet 5.5) | Codex npm 0.147.0 | 판정 |
|---|---|---|---|
| 호출 통과 | 71개 중 71개, 성공 호출 71개 | 71개 중 71개, 성공 호출 71개 | 통과 |
| 종료 코드·작업 폴더 밖 파일 | G9~G14 종료 코드 모두 0, 폴더 밖 파일 0 | 같음 | 통과 |
| 오류 응답 | `sheet.render`·`sheet.export`·`sheet.index` 의 CONFIRM_REQUIRED(정상 확인 절차)만. 확인 토큰으로 확정해 파일 3개 생성 | 같음 | 통과 |
| 도구 호출 수(묶음별) | 33, 35, 26, 21, 37, 24 (합 176) | 37, 52, 49, 28, 48, 30 (합 244) | - |
| 응답 크기 | 도구 응답 최대 12,299B(G11 지붕·솔리드), 전송 바이트 최대 46,353B(G14, PNG 이미지 포함). 잘림 오류 0 | 도구 응답 최대 13,236B | 통과 |
| 시간 | 핸드셰이크 1.3~1.7초(`server/discover`), 도구 응답 중앙값 3.1~6.4ms, 최대 약 1.3초(`dobim_project`). 서버 stdout 비JSON 줄 0 | initialize 1.36~1.52초, 중앙값 2.7~7.9ms, 최대 1.4초. 비JSON 줄 0 | 통과 |
| 협상 프로토콜 | 2026-07-28 | 2025-06-18 | 클라이언트별 코어 분기 없음(린트 재측정 10-4) |

8절의 54개 측정과 합치면 두 클라이언트 모두 레지스트리 125개를 한 번 이상 호출·통과했다(54개는 8절 당시 코어, 71개는 이번 코어 기준이라 같은 코어로 125개를 한 번에 잰 것은 아니다).

### 10-2. 탐색 15개와 이미지 판정

| 항목 | 결과 | 판정 |
|---|---|---|
| 탐색 15개(Claude) | 15/15 성공(100%), 평균 검색 호출 4.4, 평균 도구 호출 7.73. 스키마 오류 3건(N02 1, N09 2)은 모델이 스스로 고쳐 성공, N15는 확인 토큰 단계까지 끝냄. 기준 90% 충족 | 통과 |
| 탐색(Codex) | 하지 않음(사용자 승인된 범위 축소). 근거: 7절 Codex npm 탐색 19/20(기준 통과)이 이미 있고 비용이 가장 큼(예상 약 500만 토큰). 새 커맨드에 대한 Codex 탐색 값은 없다 | 미측정 |
| 이미지(Claude) | I1 평면 뷰 문 기호 세기(정답 3), I2 시트 표제란 도면번호·프로젝트명 읽기. 도구 결과에 PNG 블록이 실렸고 모델이 값을 정확히 썼다. I2 성공. I1은 1차에서 `element.query` 호출이 금지 호출로 잡혀 실패 판정이었으나, 원인은 시험 설계 결함이다(벽 이름을 id 로 바꾸려는 조회였고, 값 3은 그 조회 전의 dry-run 호출에 이미 이미지만 보고 넣었음). 지시문에 벽 id 를 주고 I1 만 다시 돌려 성공. 두 번의 I1 결과를 모두 남긴다 | 통과(1차 I1 실패 기록 포함) |
| 이미지(Codex) | I1·I2 모두 성공, 도구 결과에 PNG 블록, 금지 호출 0 | 통과 |

`dobim_render` 는 이제 `view.image`·`sheet.image` 를 호출한다(`layer_c.py` 의 호출 분류도 그에 맞춰 바꿨다). 이미지 판정은 두 과제뿐이라 "시트가 사람 눈에 보기 좋은가"는 판정하지 않았다. 모델이 읽을 수 있는 글자·도형이 PNG 로 전달된다는 것까지만 확인했다.

### 10-3. LLM 경유 결과 동등(RA-01 11단계, 일괄 76커맨드)

| 항목 | Claude Code (3회) | Codex npm (1회) |
|---|---|---|
| 최종 model.hash(state·derived·ifc·rev 76) | 3회 모두 CLI 기준과 같음 | 같음 |
| 일괄 76커맨드 인자 글자 일치 | 3회 모두 일치 | 일치 |
| 내보낸 IFC 의미 비교 | 3회 모두 차이 0(요소 28개) | 차이 0(요소 28개) |
| 작업 폴더 밖 쓰기 | 0 | 0 |
| 호출 순서가 스크립트와 일치 | 1/3. 나머지 2회는 순서만 바뀜: 한 번은 서로 의존 없는 7단계(폴더 밖 쓰기 시도)를 6단계(확인 토큰으로 확정)보다 먼저, 다른 한 번은 6단계를 맨 뒤로 미룸(추가·누락 호출은 0) | 일치 |

해석: 결과(상태·IFC)의 동등은 통과다. "호출 순서까지 스크립트와 같다"는 엄격 기준은 Claude 에서 3회 중 1회만 충족했다. 확인 토큰을 받는 5단계는 항상 6단계보다 앞이었고, 어긴 것은 서로 독립인 7단계 등의 위치다. 합격 기준을 결과 동등으로 두면 통과, 호출 순서 동일까지 요구하면 Claude 는 미충족이다.

### 10-4. 기타와 사용량

- 분기 금지 린트 재측정: 현재 main 코어 소스 40개 파일(9절 당시 23개)에서 클라이언트별 분기 0건, 음성 대조 모두 탐지(`results/s11_lint_no_client_branch.json` 갱신).
- 이번 측정 사용 환경 참고: Codex npm 0.147 은 사용자 훅 설정 파일 형식을 읽지 못한다는 경고를 매 실행 1건 냈다(서버·도구 호출과 무관, 설정은 건드리지 않음).
- 토큰(입력+캐시+출력 합): Claude 약 310만(층 C 163.2만, 탐색 94.0만, 이미지 31.1만(I1 재실행 8.9만 포함), RA-01 3회 20.7만, 로그인 확인 0.6만), Codex 약 461만(층 C 392.2만, 이미지 54.9만, RA-01 14.1만; 층 C 중 캐시 읽기 360.4만 포함). 계획 단계 예상은 Claude 약 215만, Codex 약 640만이었다. Claude 는 층 C 가 예상의 1.8배(새 커맨드 스키마가 큼)라 전체로는 1.44배였다. Codex 는 예상보다 적었다.

### 10-5. 남은 것

- 새 커맨드 71개의 Codex 탐색, Windows 점검(10회 시작 시간·한글 경로) 125개 기준 재측정, 안내문 생성물 검사(도구 없음)는 하지 않았다.
- 54개 측정(8절)과 71개 측정(10절)은 서로 다른 코어 커밋 기준이다. 125개 전체를 같은 커밋에서 한 번에 재는 것은 하지 않았다.
- 호출 순서 동일 기준을 합격 조건에 넣을지는 사용자 결정이 필요하다(10-3).
- 이 PC 한 대, 시험당 1회(RA-01 Claude 는 3회)라 표본이 작다. 한 번씩의 결과로 안정성을 주장하지 않는다.
