# 아키텍처

기준일 2026-11-07. 코드 없이 설계만 있는 상태다. 수치 중 "목표"와 "추정"은 측정값이 아니다. 근거 문서는 `research/01-oss-candidates.md`, `research/02-revit-gap-analysis.md`이고, 그 뒤 반증으로 바뀐 사실은 `README.md` 5절에 모아 두었다.

전제로 삼은 추천안: MPL-2.0(ADR-0003), 비주거 소형 건물 + IFC 납품 담당자(ADR-0002), Windows 데스크톱(ADR-0004). 다른 선택을 하면 바뀌는 내용은 14절에 있다.

**확정 제약(사용자 지시, ADR-0011):** 모든 기능은 CLI·MCP로 제어한다. Claude와 Codex 양쪽에서 모든 기능을 쓴다. GUI·디스플레이·GPU 없는 헤드리스로 동작한다. 제어 계층은 15절에 있다.

## 1. 설계 방향

조사에서 얻은 결론은 하나다. **기능 목록은 이제 싸다.** 1인이 에이전트로 20일 만에 뷰·시트·IFC 왕복 코드를 낸 사례(ConstructCAD)가 있다. 그런 앱은 이미 여럿 있다. 비어 있는 것은 "믿을 수 있는가"다.

그래서 다음 네 가지를 먼저 지킨다.

1. 검증 코퍼스가 제품의 일부다. 왕복·증분 일치를 회귀 테스트로 항상 돌린다.
2. 변경 경로는 커맨드 하나뿐이다.
3. 도면과 일람표는 모델에서 계산되는 값이다.
4. 코어는 UI를 모른다.

## 2. 장점 조합표

형태: **링크**(프로세스 안 라이브러리), **서브프로세스**, **설계 참고**(코드 복사 없음), **대조군**(결과 비교만).

| 후보 | 가져올 것 | 형태 | 라이선스 영향 | 단계 |
|---|---|---|---|---|
| IfcOpenShell 코어·`api`·`util` | IFC 파싱·쓰기, 의미 조작, 지오메트리 반복기, 트랜잭션 | 링크(Python 휠) | LGPL-3.0. onedir 동봉, 고지, EULA 역공학 예외 | S0부터 |
| `ifctester`, `ifcdiff`, `ifcclash`, `ifcpatch` | IDS 검사, 차이 비교, 간섭, 스키마 변환 | 링크 또는 서브프로세스 | LGPL-3.0 | 1단계 |
| `ifcopenshell.draw`(HLR) | 벡터 도면 기준 구현 | 링크, S3 비교 대상 | LGPL-3.0 | S3 |
| Bonsai | 네이티브 IFC 편집 흐름, 도면 구성(underlay/linework/annotation 3층), 리스너 사용법 | 설계 참고(문서·동작만), 대조군 | GPL-3.0. 소스를 에이전트에 넣지 않음. 어댑터는 별도 GPL 패키지 | S0, 선택 |
| FreeCAD BIM | NativeIFC 접근, TechDraw 동작 | 대조군, 설계 참고 | LGPL-2.1. 앱 임베드는 하지 않음 | S0 |
| OCCT | 정밀 형상, HLR | IfcOpenShell 경유 간접 사용 | LGPL-2.1 + 예외. 고지 | S1 |
| Manifold | 표시용 메시 불리언 | 링크(`manifold3d`) | Apache-2.0 | S1 |
| Clipper2 / shapely | 평면 조인·오프셋 | 링크 | BSL-1.0 / BSD 계열(확인 필요) | S1 |
| ezdxf | DXF 입출력 | 링크 | MIT | 1단계 |
| BimCraft | 패밀리 레시피 구조, `IfcGroup` + Pset 저장, 저널식 실행 취소, 실시간 공동편집 클라이언트(`live-client.js`)의 rebase·거부 롤백 | 설계 참고가 기본(CI 적색, 스냅숏 미러). 코드 재사용은 필요할 때만 | Apache-2.0. NOTICE 유지. 상표 주의 | S0, S4, S8 |
| ConstructCAD | 뷰=모델 질의 enum 구조, 표현식 컴포넌트 | 동작 관찰만, 대조군 | GPL-3.0. 소스 참조 금지 | S0 |
| ifc-lite | WebGPU 렌더러, `drawing-2d`, `create`, CRDT 협업(저자 주장) | S6에서 링크 후보, 대조군 | MPL-2.0. 파일 단위 공개. retained 경로는 usd-brep 등 제3자 고지가 추가되고 네이티브 워커의 최종 링크 라이선스는 저장소 문서상 미해결이므로 기본 exact 경로만 사용 | S0, S6 |
| That Open Fragments·components | 대형 모델 뷰어 | S6 후보 | MIT | S6 |
| web-ifc | ifc-lite를 안 쓸 경우 브라우저 파서 | 보류 | MPL-2.0 | 필요 시 |
| Pascal Editor | MCP 도구 설계, 에이전트 흐름 | 설계 참고 | MIT | 2단계 |
| `ifcmcp`·`ifcedit`(IfcOpenShell) | MCP 서버 구조, `list`·`docs`·`run` CLI 구조(API 350개 이상 자동 노출) | 설계 참고 또는 링크 | LGPL-3.0 | S1 |
| ConstructCAD 문서(`docs/automation.md`) | 하나의 카탈로그로 CLI·스크립트·MCP를 제공하는 구조, 매크로 기록 | 문서 참고만, 대조군 | GPL-3.0. 소스 참조 금지 | S0 |
| 공식 `mcp` SDK, pydantic | MCP 서버, 스키마 | 링크 | MIT | S1 |
| resvg | SVG → PNG | 링크 또는 서브프로세스 | Apache-2.0(저장소 SPDX) | S10 |
| import-linter | 의존 방향 강제 | 개발 도구 | BSD | S1 |
| Speckle | 객체 버전 모델, 이후 연동 커넥터 | 설계 참고, 나중에 연동 | Apache-2.0, EE 디렉터리 제외 | 4단계 |
| ifcmerge, IfcGit | IFC 병합 접근의 한계 파악 | 설계 참고 | ifcmerge 라이선스 필드 없음, 확인 필요 | S8 |
| IDS, BCF | 납품 요구 검사와 이슈 교환 표준 | 표준 구현 | 표준 문서 조건 확인 | 1단계 |
| rvt-rs | 근사 가져오기 실험 | 서브프로세스, 연구 트랙 | Apache-2.0. 법적 위험 별도 | 연구 |
| xeokit, BIMserver, Chili3D, bldrs | 사용 안 함 | - | AGPL-3.0 (L2가 아니면 제외) | - |

MPL 이외를 고르면 달라지는 부분은 ADR-0003 표를 본다.

## 3. 구성

```text
 클라이언트: Claude · Codex · 스크립트·CI · Tauri 앱(선택 패키지)
                       │  CLI / stdio MCP / Python API
 ┌─────────────────────┴───────────────────────────┐
 │ 코어 패키지 (GUI·디스플레이·GPU 의존 없음)       │
 │  커맨드 레지스트리 ── 추가 전용 로그             │
 │       │                                          │
 │  의도 저장소(SQLite) ◄─GlobalId─► IFC(메모리)    │
 │       │                              │           │
 │  재생성 그래프: 조인 → 형상 → IFC 투영           │
 │                   └→ 뷰 → 선분 → 시트 → PDF/DXF  │
 │                   └→ 일람표 · 태그 · 치수 · 검사 │
 │  패밀리 평가기 · 형상 서비스 · 캐시              │
 └──────────────────────────────────────────────────┘
       IfcOpenShell · Manifold · Clipper2 · ezdxf (병목만 Rust)
```

프로젝트 저장: `프로젝트.dobim`(제안 확장자, ADR-0010) = SQLite 한 파일. 로그, 의도, 패밀리 정의, 뷰, 형상 캐시, 최신 IFC 스냅숏을 담는다. `model.ifc`는 언제든 내보낸다. 외부에서 수정된 IFC는 GlobalId 기준 비교 후 커맨드로 변환해 받아들인다.

## 4. 데이터 정본

결정 근거는 ADR-0005. **정보 종류마다 정본은 하나**다.

| 정보 | 정본 |
|---|---|
| 파라미터, 호스팅·조인, 패밀리 참조, 뷰·시트·주석 정의 | 의도 저장소 |
| 속성셋, 분류, 재료, 공간 구조, 타입 관계 | IFC |
| 저작 요소의 형상·배치 | 파생물 |
| 외래 요소의 형상 | IFC, 동결 |

스키마 초안:

```sql
-- 요소: IFC GlobalId와 1:1. origin으로 저작/외래/분리 상태를 구분
CREATE TABLE element (
  id TEXT PRIMARY KEY,          -- IFC GlobalId
  kind TEXT NOT NULL,           -- wall, door, foreign ...
  ifc_class TEXT NOT NULL,
  origin TEXT NOT NULL,         -- authored | foreign | detached
  family_id TEXT,
  params TEXT NOT NULL,         -- JSON. 재생성 입력
  projection_hash TEXT,         -- 마지막 IFC 투영 해시. 외부 변조 감지
  -- MVP에서 UI는 없지만 자리를 남기는 필드
  workset_id TEXT,
  phase_created TEXT,
  phase_demolished TEXT,
  design_option_id TEXT
);

CREATE TABLE relation (kind TEXT, src TEXT, dst TEXT, data TEXT);  -- hosts, joins, on_level

-- 커맨드 로그: 의미 수준 연산만 저장, 파생값은 저장하지 않음
CREATE TABLE op_log (
  seq INTEGER PRIMARY KEY,
  txn_id TEXT, actor TEXT, base_rev INTEGER, ts TEXT,
  op TEXT NOT NULL,             -- wall.move, pset.set ...
  args TEXT NOT NULL, inverse TEXT NOT NULL
);

CREATE TABLE view_def (id TEXT PRIMARY KEY, def TEXT NOT NULL);
CREATE TABLE sheet (id TEXT PRIMARY KEY, def TEXT NOT NULL);
CREATE TABLE annotation (id TEXT PRIMARY KEY, view_id TEXT, def TEXT NOT NULL);
CREATE TABLE family_def (id TEXT, version INTEGER, hash TEXT, body TEXT, PRIMARY KEY (id, version));
```

IfcOpenShell은 API 리스너(`add_pre_listener`, `add_post_listener`)와 파일 트랜잭션(`begin_transaction`, `undo`, `redo`)을 이미 제공한다(소스 확인). 외부 호스트의 변경을 잡을 때와 트랜잭션 롤백에 쓴다. 다만 실행 취소의 기준은 커맨드 로그다.

## 5. 의존성 그래프와 재생성

결정 근거는 ADR-0006. 양방향 연관성의 실체는 이 그래프다. Revit의 "양방향"은 사실 두 방향의 편집 입력(평면에서 끌기, 일람표에서 값 수정)이 같은 모델로 들어가는 구조다. 화면은 모두 출력이다.

```python
# 초안: 질의는 순수 함수이고, db 읽기가 자동으로 의존성이 된다
@query
def wall_footprint(db, wall_id):
    axis = db.params(wall_id)["axis"]
    joins = [wall_join(db, j) for j in db.relations("joins", wall_id)]
    return trim_footprint(axis, db.wall_type(wall_id).thickness, joins)

@query
def view_cut_lines(db, view_id, element_id):
    # 요소·뷰 쌍 단위로 캐시. 다른 요소가 바뀌어도 재사용된다
    return cut_element(element_solid(db, element_id), db.view(view_id).cut_plane)
```

노드 예시: `wall.footprint`, `join(W1,W2)`, `wall.body`, `door.placement`, `ifc.projection(E)`, `view.visible_set(V)`, `view.cut_lines(V,E)`, `view.proj_lines(V,E)`, `dim.value(D)`, `tag.text(T)`, `schedule.rows(S)`, `sheet.render(H)`.

벽을 옮기는 경우의 흐름:

1. `wall.move` 커맨드가 입력을 바꾸고 리비전을 올린다.
2. 벽 축선의 의존 노드가 무효로 표시된다.
3. 호스팅된 문은 축선 기준 좌표라 위치가 따라간다. 활성 뷰가 선분을 요청하는 시점에 필요한 것만 계산한다.
4. 조인 결과가 같으면 인접 벽의 계산은 멈춘다.
5. 일람표는 벽 길이 값이 달라졌을 때만 다시 계산한다.

### 지연 목표 (측정값 아님)

| 층 | 대상 | 5,000요소 p95 목표 |
|---|---|---|
| T0 동기 | 입력, 의미 데이터, 일람표 값 | 50ms |
| T1 준동기 | 활성 뷰의 형상·선분 | 300ms |
| T2 배경 | 다른 뷰, 시트. 갱신 전에는 "오래됨" 표시 | 수 초 |
| T3 요청 시 | PDF·DXF·IFC 내보내기 | 수십 초 |

### 필수 불변식

`증분 재계산 결과 == 커맨드 로그 전체 재생 결과`. 임의 커맨드열로 항상 검사한다. 이 테스트가 없으면 도면이 언제 틀렸는지 알 수 없다.

## 6. 형상

결정 근거는 ADR-0007. 프리즘 경로 → Manifold → IfcOpenShell 반복기 순으로 내려간다. 커널은 만들지 않는다.

위상 명명 문제를 피하는 규칙이 중요하다. **면과 모서리를 인덱스로 참조하지 않는다.** 주석과 호스팅은 `wall:<id>#face=exterior` 같은 의미 참조만 쓴다. 벽 코너가 다시 계산돼 면 개수가 바뀌어도 치수가 끊기지 않는다.

조인은 MVP에서 L자, T자, 십자, 끝점 정리까지 한다. 재료 층별 조인 우선순위는 코어 층까지만 지원하고 정밀 처리는 뒤로 미룬다.

## 7. 뷰, 시트, 일람표

뷰는 그림이 아니라 저장된 질의다.

```json
{
  "type": "plan",
  "level": "2F",
  "cut_height": 1.2,
  "view_depth": -0.3,
  "crop": [0, 0, 12, 8],
  "scale": 100,
  "detail": "medium",
  "template": "KR-건축-평면",
  "filters": [],
  "phase": null
}
```

도면 생성 경로:

1. **평면**: 프리즘 요소는 프로파일이 곧 절단면이다. 해석적으로 빠르게 얻는다. 문·창은 패밀리의 평면 기호를 쓴다. 나머지는 메시 절단.
2. **단면·입면**: 요소별로 특징 모서리를 뽑아 캐시하고, 뷰별로 가림을 계산한다. 요소가 바뀌면 이전·이후 경계 상자가 덮는 영역만 다시 계산한다.
3. **후보 구현**: S3에서 `ifcopenshell.draw`, 자체 메시 HLR, ifc-lite `drawing-2d`를 같은 건물로 비교한다. 있는 것을 먼저 쓴다.
4. **스타일**: 요소 분류 + 절단/투영 + 축척 → 선 굵기·해치 표. 한국 도면 표준 원문은 미확인이라 S9에서 찾는다.

Bonsai의 벡터 HLR이 이미 있으므로 **벡터 출력 자체는 차별점이 아니다.** 차별점은 요소별 캐시로 얻는 증분 갱신과 주석 연관성이다.

시트는 도곽 + 뷰포트 목록이다. 참조 표기(시트 번호↔단면 기호)는 파생 질의로 자동 갱신한다. 출력은 SVG(내부 표준) → PDF, DXF다. PDF 변환기와 한글 글꼴 임베드는 S3에서 정한다. DWG는 직접 쓰지 않는다. ODA 변환기는 약관이 불명확하고 사용자 환경 설치가 필요하므로 선택 기능으로 둔다.

일람표는 필터·정렬·집계 정의 + `schedule.rows` 질의다. 일람표에서 값을 고치면 해당 요소의 커맨드로 들어간다.

주석: 치수는 의미 참조 2개 + 배치 오프셋, 태그는 대상 요소 + 표시 필드 + 위치. 값은 항상 파생이다. 대상이 삭제되면 고아 주석으로 표시하고 조용히 지우지 않는다.

## 8. 렌더링

- 코어는 요소별 메시 델타를 이진 버퍼로 보낸다. 뷰어가 IFC를 다시 파싱하지 않는다.
- 전송은 Tauri 사이드카의 로컬 WebSocket(MeetScribe에서 쓴 FastAPI 계열 재사용 가능). 지연은 S6에서 측정.
- 3D는 WebView2 + WebGPU, 폴백 WebGL2. 후보 비교는 S6.
- 2D 편집 뷰는 PixiJS. 선분은 뷰포트 단위 가상화. 인쇄 품질은 이 경로가 아니라 7절 벡터 경로가 책임진다.

## 9. 패밀리

결정 근거는 ADR-0008. 요약 예시:

```json
{
  "id": "door.single",
  "version": 1,
  "ifc": { "class": "IfcDoor", "predefined": "DOOR" },
  "host": { "kinds": ["wall"], "cut": "opening" },
  "params": {
    "width":  { "kind": "type", "unit": "mm", "default": 900, "min": 600, "max": 1500 },
    "height": { "kind": "type", "unit": "mm", "default": 2100 },
    "frame":  { "kind": "type", "unit": "mm", "default": 50 },
    "swing":  { "kind": "instance", "enum": ["L", "R"], "default": "L" }
  },
  "refs": {
    "left":  { "axis": "x", "at": "-width/2" },
    "right": { "axis": "x", "at": "width/2" }
  },
  "solids": [
    { "op": "extrude", "profile": "rect(left, right, 0, height)", "depth": "frame*2" }
  ],
  "plan_symbol": { "1:100": [{ "arc": { "center": "left", "radius": "width" } }] },
  "psets": { "Pset_DoorCommon": { "IsExternal": false } }
}
```

평가 결과는 그래프의 질의 값이다. 같은 입력 해시면 메시를 공유하므로 인스턴스 렌더링과 `RepresentationMap`이 자연스럽게 연결된다. 레시피는 텍스트이므로 에이전트 생성과 diff, 리뷰가 쉽다.

## 10. API와 확장

공개 표면은 모두 커맨드 레지스트리를 탄다(15절).

```python
txn = project.begin("벽 이동")
txn.run("wall.move", id=wall_id, dx=600)
txn.commit()                       # 실패하면 전체 롤백
rows = project.run("schedule.rows", name="문 일람표")
```

- **CLI·MCP**: 레지스트리에서 생성한다. 15절.
- **Python 플러그인**: 진입점 등록, 신뢰 코드. 커맨드를 추가로 등록할 수 있고, 등록하면 CLI·MCP에 자동으로 나타난다. IFC를 직접 수정하지 못하고 커맨드만 낸다. 커밋 이벤트를 구독한다. 로드 조건은 15.8절.
- **패밀리·IDS·뷰 템플릿**: 데이터이므로 샌드박스가 필요 없다.
- API는 의미 버전으로 관리한다. 1단계 동안은 불안정으로 표시한다.

## 11. 협업을 막지 않는 조건

MVP에 협업은 없다. 그러나 다음 규칙은 첫 줄부터 지킨다. 나중에 넣을 수 없는 것들이다.

1. 요소 ID는 전역 고유(GlobalId). 파생 엔티티 ID는 결정적으로 생성.
2. 모든 변경은 의미 수준 커맨드. 필드 단위까지 알 수 있어야 한다.
3. 로그에 `actor`, `base_rev`를 처음부터 기록.
4. 파생값은 로그와 병합 대상에서 제외.
5. 재생성은 결정적. 두 사람이 같은 로그를 재생하면 같은 해시가 나와야 한다.
6. `workset_id`, 위상, 설계 옵션 필드를 처음부터 둔다(02문서 16번 지적 반영).

협업 도입 순서(4단계): 서버가 로그를 받아 순서를 정한다 → 요소 단위 차용(비관적 잠금, Revit 워크셋 방식이 개념과 구현 모두 쉬움) → 필드 단위 자동 병합 → 재생성으로 의미 충돌 검사(예: 호스트 벽 삭제 + 문 수정) → 필요 시 CRDT. S8에서 로그 병합을 작은 규모로 미리 시험했다(결과는 `spikes/s8-merge/report.md`, 코어의 `merge.preview`·`merge.apply`). BimCraft의 공동편집 클라이언트(보류 큐 rebase, 거부 시 롤백, 사용자별 실행 취소, holds)는 참고할 선례다. 서버는 그 저장소에 없다.

## 12. 대형 모델 성능

| 단계 | 목표(설계 목표, 측정 전) | 수단 |
|---|---|---|
| 저작 MVP | 5,000요소 | 증분 그래프, 요소별 캐시, 활성 뷰 우선 |
| 1단계 읽기 전용 | 50,000요소의 Revit 내보내기 IFC | 메시 캐시, 타입 인스턴싱, 뷰 범위 공간 인덱스, 멀티프로세스 형상 계산 |
| 이후 | 200,000요소 | 링크 모델 분할 로드, HLOD, 렌더러 타일링, 그래프·HLR Rust 이식, 파서 교체 검토(ifc-lite 네이티브) |

원칙은 다음과 같다.

- 전체 IFC를 매번 다루지 않는다. 내용 해시로 키를 잡은 형상 캐시를 SQLite에 저장한다.
- 그래프는 필요한 노드만 깨운다. 뷰 하나의 계산은 뷰 범위 요소 수에 비례해야 한다.
- 추측으로 Rust로 옮기지 않는다. 프로파일 결과가 나온 부분만 옮긴다.
- `ifcopenshell.file` 전체 로드의 메모리 한계는 S1에서 측정한다.

## 13. 가장 위험한 설계 결정

위험이 큰 순서다.

| # | 결정 | 틀렸을 때 | 되돌리기 비용 | 검증 |
|---|---|---|---|---|
| 1 | 의도 저장소와 IFC의 정본 분담 | 불일치 버그가 끝없이 나오고 왕복 품질을 믿을 수 없음 | 매우 높음 | S0(Pset 생존), S1(차등 테스트) |
| 2 | 수요 구동 증분 그래프를 Python으로 | 지연 목표 미달, 도면이 틀림 | 높음(Rust 이식) | S1 |
| 3 | 요소별 캐시로 증분 도면이 가능하다는 가정 | 가림 계산이 전역이라 캐시 효과가 없고 "라이브"가 불가능 | 높음(범위 축소 필요) | S3 |
| 4 | 패밀리에 구속 해석기를 넣지 않음 | 실제 패밀리가 표현되지 않음 | 중간 | S4 |
| 5 | 의미 참조만 쓰는 주석 | 복잡한 형상에서 참조 부족 | 중간 | S5 |
| 6 | IFC 네이티브 쐐기로 충분한 수요 | 아무도 안 씀 | 기술 문제 아님 | 실무자 확인(D2) |
| 7 | 메타 도구로 전 커맨드 노출 | 에이전트가 커맨드를 못 찾거나 스키마를 자주 틀림 | 낮음(직접 도구 추가) | S11 |
| 8 | Python 코어의 stdio 서버 | 시작 지연·stdout 오염으로 Windows에서 불안정 | 중간(네이티브 실행기) | S11 |

## 14. 다른 선택을 하면 바뀌는 것

| 결정 | 다른 선택 | 아키텍처 변화 |
|---|---|---|
| 라이선스 L2 (GPL) | Bonsai 코드 직접 사용 가능 | 2절 "설계 참고" 칸이 링크로 바뀜. 코어를 Bonsai 내부 모듈로 둘 수도 있음 |
| 라이선스 L3 (폐쇄) | Bonsai 어댑터 포기 | EULA 조항 필요. onedir 필수. 외부 기여 없음. ODA 상용 검토 가능 |
| 플랫폼 웹 우선 | 코어를 TS/Rust로 | ifc-lite가 기반이 됨. 3~5·9~11절 개념은 유지, 구현 전부 교체 |
| 경로 A (Bonsai) | Tauri 앱 없음 | 8절 삭제. 뷰어와 편집은 Blender. 코어는 그대로. Bonsai 조작자는 15절 제어 대상이 되지 못함 |
| 타깃 T2 (학습·연구) | 납품 검사 제외 | S9 제외. 1단계 산출물을 "증분 도면 라이브러리"로 축소 |

## 15. 제어 계층

결정 근거와 클라이언트 비교는 ADR-0011. 이 절의 요구사항은 승인됐고 구현 방식은 제안 상태다.

### 15.1 커맨드 레지스트리

기능은 레지스트리 항목 하나로 존재한다. CLI, MCP, GUI, Python API, 문서, 테스트가 모두 여기서 나온다. 4절의 커맨드 로그와 하나로 합친다. 변경 커맨드는 로그에 남고, 조회는 남지 않는다.

```python
@command(
    name="wall.move", kind="mutation", risk="write",
    idempotent=False, long_running=False,
    since="0.1", group="model.wall",
    summary="벽을 평행 이동한다",
    examples=[{"id": "<GlobalId>", "dx": 600}],
)
def wall_move(ctx, args: WallMoveArgs) -> MutationResult: ...
```

| 필드 | 값 |
|---|---|
| `kind` | `query`, `mutation`, `session`, `job` |
| `risk` | `read`, `write`, `destructive`, `code_exec` |
| 입력·출력 | pydantic(MIT) 모델 → JSON Schema 2020-12 |
| `examples` | 패리티 테스트의 입력으로 재사용 |

규칙은 다음과 같다.

- GUI 패키지는 코어 내부를 import하지 못한다. 공개 클라이언트로 커맨드만 호출한다.
- 커맨드 없는 메뉴 항목은 빌드 실패로 처리한다.
- GUI 조작은 매크로(커맨드 목록)로 기록할 수 있다. ConstructCAD 문서에 같은 구조의 선례가 있다. 소스는 참조하지 않는다.

### 15.2 "기능"의 범위

**판정 규칙: 저장 내용이나 출력 결과에 영향을 주면 커맨드다.**

| 영역 | 커맨드 예시 |
|---|---|
| 프로젝트 | `project.new`, `project.open`, `project.save_as`, `project.info`, `project.snapshot` |
| 모델링 | `wall.create`, `door.place`, `level.create`, `join.set`, `element.delete` |
| 조회 | `element.get`, `element.query`, `relation.list`, `model.stats` |
| 뷰·시트·주석 | `view.create`, `view.set_camera`, `sheet.place_view`, `dim.create`, `tag.create` |
| 일람표 | `schedule.create`, `schedule.rows`, `schedule.set_cell` |
| 패밀리 | `family.validate`, `family.install`, `family.type_set`, `family.preview` |
| 입출력 | `ifc.import`, `ifc.export`, `pdf.export`, `dxf.export`, `sheet.export` |
| 검증 | `ids.check`, `kr.check`, `clash.run`, `model.validate`, `roundtrip.check` |
| 이력 | `history.undo`, `history.redo`, `history.list`, `history.diff` |
| 설정 | `settings.get`, `settings.set`, `template.apply`, `units.set` |
| 렌더 | `render.view`, `render.sheet`, `render.3d`, `render.diff` |
| 시스템 | `commands.search`, `commands.describe`, `job.get`, `job.cancel`, `plugin.list` |

**순수 GUI 상호작용**은 다음처럼 다룬다.

| 상호작용 | 처리 |
|---|---|
| 카메라 궤도·확대 | 제외. 저장 뷰의 카메라는 `view.set_camera`, 렌더는 `render.3d`의 인자로 가능 |
| 끌기 미리보기, 호버, 스냅 표시 | 제외. 확정 결과는 커맨드. 스냅 계산은 `geom.snap` 조회로 제공 |
| 선택 | 임시 선택은 제외. 선택 집합 저장은 `selection.save`. GUI가 붙어 있을 때 `session.selection`을 읽을 수 있고, 없으면 `NO_GUI_ATTACHED`를 반환 |
| 패널 배치, 테마, 창 크기, 단축키 | 제외. 프로젝트와 출력에 영향이 없음 |

제외 항목은 `gui_exempt.yaml` 파일로 관리한다. 항목마다 이유를 적는다. 검사기는 제외 동작이 프로젝트 저장소를 건드리면 실패시킨다.

### 15.3 CLI

```text
dobim list [--group model.wall] [--format json]
dobim schema wall.move
dobim run wall.move -p 프로젝트.dobim --json @args.json [--dry-run]
dobim run wall.move -p 프로젝트.dobim id=<GlobalId> dx=600
dobim script build.jsonl -p 프로젝트.dobim [--atomic]
dobim job get <id>
dobim mcp serve --workspace <dir>
dobim install-agent --client claude|codex|all
```

ifcedit의 `list`·`docs`·`run` 구조와 같은 형태다. 350개 이상의 API를 이 방식으로 노출한 선례다.

**무인 실행 규칙**

- 대화형 프롬프트 금지. stdin이 닫혀 있어도 동작한다.
- stdout은 결과 JSON만, stderr는 사람용 로그와 진행 상황만 쓴다. 로그 파일은 `--log-file`로 지정한다.
- 색상과 진행 막대는 TTY일 때만 쓴다.
- 입력이 부족하면 묻지 않고 실패한다.

| 종료 코드 | 의미 |
|---|---|
| 0 | 성공 |
| 1 | 커맨드 실패 |
| 2 | 사용법·스키마 오류 |
| 3 | 검사 실행은 성공, 위반 발견 |
| 4 | 리비전 충돌·잠금 |
| 5 | 대상 없음 |
| 6 | 권한·경로 거부, 확인 필요 |
| 7 | 작업 시간 초과·취소 |
| 70 | 내부 오류 |

배치는 JSONL 스크립트로 한다. `--atomic`이면 전체가 하나의 트랜잭션이다.

### 15.4 MCP 노출

기본 도구는 8개다. **모든 커맨드는 이 도구로 닿는다.**

| 도구 | 어노테이션 | 역할 |
|---|---|---|
| `dobim_commands_search` | read-only | 키워드·그룹 검색, 이름과 한 줄 설명 반환 |
| `dobim_command_describe` | read-only | 커맨드 하나의 스키마와 예시 |
| `dobim_query` | read-only | `risk=read` 커맨드 실행 |
| `dobim_execute` | write | `risk=write` 커맨드 또는 원자적 배치 실행, `dry_run` 지원 |
| `dobim_execute_destructive` | destructive | `risk=destructive` 실행, 확인 토큰 필요 |
| `dobim_job` | read-only | 작업 상태 조회. 취소는 `dobim_execute`의 `job.cancel` |
| `dobim_render` | read-only | 이미지 반환 |
| `dobim_project` | write | 열기·생성, 핸들 발급 |

`code_exec` 커맨드는 서버를 `--allow-code`로 시작했을 때만 `dobim_execute_destructive`로 실행한다(15.8절).

이 방식을 고른 이유는 다음과 같다.

- 두 클라이언트 모두 도구 지연 로딩 성향이지만 Codex 쪽 공식 확인이 없다. 서버가 직접 제어하면 의존하지 않아도 된다.
- Codex `tool_input_schema_max_bytes` 기본값이 5,000바이트다. 메타 도구는 일반 객체 입력이므로 걸리지 않는다.
- 위험 등급별로 도구가 갈려 Claude 권한 규칙과 Codex `writes` 모드가 그대로 동작한다.
- 대가로 클라이언트가 인자 스키마를 미리 검증하지 못한다. 서버가 검증하고, 실패하면 JSON Pointer 경로와 스키마를 오류에 담아 돌려준다.

`--tools=direct`로 커맨드별 도구를 내는 프로필도 생성할 수 있지만 기본이 아니다. 시험 범위에도 별도로 넣는다.

### 15.5 에이전트가 쓸 수 있는 조건

**결과 봉투** (CLI stdout, MCP text + structuredContent에 같은 JSON)

```json
{
  "ok": true,
  "rev": 1842,
  "result": {},
  "affected": { "created": [], "modified": ["2O2Fr$t4X7Zf8NOew3FLOH"], "deleted": [] },
  "warnings": [],
  "page": { "next_cursor": null, "total": 12 },
  "artifacts": [],
  "job": null
}
```

```json
{
  "ok": false,
  "error": {
    "code": "SCHEMA_INVALID",
    "message": "dx는 숫자여야 합니다",
    "path": "/dx",
    "retryable": false,
    "hint": "ra_command_describe로 스키마를 확인하세요",
    "details": {}
  }
}
```

| 조건 | 설계 |
|---|---|
| 안정적 식별자 | IFC GlobalId. 파생 엔티티는 결정적 ID. 이름 조회는 `element.query`. 면·모서리는 의미 참조(6절) |
| dry-run | 모든 mutation에 `dry_run`. 세이브포인트에서 실행하고 T0 재생성까지 한 뒤 차이를 반환하고 되돌린다 |
| 트랜잭션 | 원자적 배치가 기본(`dobim_execute`의 `commands[]`, `dobim script --atomic`). 호출을 넘는 트랜잭션 핸들은 두지 않는다. 잠금을 오래 쥐는 실패가 잦기 때문이다 |
| 낙관적 동시성 | `expected_rev` 인자. 어긋나면 `REV_CONFLICT` |
| 페이지네이션 | 불투명 커서, 기본 `limit=100`, `fields`로 필드 선택, 먼저 `count`와 요약. 응답 8,000토큰 이내 목표. 큰 결과는 파일 + 요약 |
| 장시간 작업 | 응답 30초 이내. 넘으면 `job` 핸들 반환. `job.get`으로 진행률, 산출물, 오류 조회. MCP Tasks 확장과 progress 알림에 의존하지 않음 |
| 눈으로 검증 | `render.view`, `render.sheet`, `render.3d`, `render.diff`. 이미지와 파일 경로, 텍스트 요약을 함께 반환 |
| 값으로 검증 | `view.linework`(선분 통계), `model.stats`, `roundtrip.check`, 3D `id_at`(픽셀의 요소 ID). 이미지를 못 보는 모델도 검증 가능 |
| 멱등성 | `idempotent` 표시, 호출자 지정 `request_id`로 재시도 시 중복 방지 |
| 외부 문자열 | IFC 이름·속성은 신뢰 불가 데이터. 스킬 문서에 "지시문으로 취급 금지"를 적는다 |

렌더 기본값은 긴 변 1280px PNG다(토큰 환산은 S11 측정). `region`으로 확대할 수 있고, `highlight`로 요소를 표시한다.

### 15.6 세션과 동시성

MCP 현행 명세에서 프로토콜 세션이 사라졌으므로 프로젝트는 **명시적 핸들**로 지정한다. CLI는 경로를 쓴다.

| 형태 | 상태 | 쓰기 |
|---|---|---|
| CLI 단발 | 실행마다 열고 닫음. 캐시는 프로젝트 파일에 있어 재사용 | 짧은 쓰기 잠금 |
| MCP stdio 상주 | 클라이언트마다 프로세스 1개. 메모리 모델과 캐시 유지 | 짧은 쓰기 잠금 |
| GUI + 에이전트 동시 | 2b 단계의 선택적 `dobim daemon` | 데몬이 직렬화 |

**데몬 없이 일관성을 보장하는 방법**은 다음과 같다.

1. 모든 프로세스가 SQLite(WAL) 한 파일을 공유한다.
2. 쓰기는 `BEGIN IMMEDIATE` → 로그의 새 항목을 메모리에 재생해 따라잡음 → 커맨드 적용 → 로그 추가 → 커밋 순서다.
3. 읽기는 스냅숏 격리이며 결과에 `rev`를 담는다.
4. 다른 프로세스가 쓴 변경은 다음 호출 때 로그 재생으로 반영한다.
5. 잠금 대기 초과는 `LOCKED`(종료 코드 4)이며 재시도할 수 있다.

실행 취소는 커맨드 로그의 보상 항목이므로 CLI와 MCP를 넘나들어도 동작한다. 다른 행위자의 커맨드를 되돌릴 때는 `warnings`로 알린다. 네트워크 드라이브에서는 WAL을 믿지 않으므로 경고 후 거부한다.

### 15.7 클라이언트 동등성과 어댑터

```text
코어(커맨드) ← 프로토콜 계층(CLI, MCP) ← 어댑터(claude, codex)
```

어댑터가 하는 일은 등록과 문서 생성, 클라이언트 고유 메타데이터뿐이다. 코어와 프로토콜 계층에서 클라이언트별 분기를 금지하고 린트로 막는다. Claude 전용 `anthropic/maxResultSizeChars`처럼 추가 메타 필드는 어댑터에서만 넣고, 없어도 기능이 동작해야 한다.

**공통 부분집합 규칙**: stdio, tools, 텍스트 JSON, 이미지. resources, prompts, elicitation, sampling, roots, Tasks 확장, progress 알림에 **기능을 의존시키지 않는다**.

**등록**

```powershell
# 코어 설치 후. 설정 파일을 직접 편집하지 않아도 되게 한다
dobim install-agent --client all --scope user
```

내부적으로 다음을 실행한다(식별자는 제안, ADR-0010). Windows에서는 `cmd /c` 없이 실행 파일 절대 경로를 쓴다.

```powershell
claude mcp add --scope user --transport stdio dobim -- <dobim.exe 절대 경로> mcp serve --workspace <dir>
codex mcp add dobim -- <dobim.exe 절대 경로> mcp serve --workspace <dir>
```

Codex는 `startup_timeout_sec = 30`과 큰 커맨드 대비 `tool_timeout_sec`을 `config.toml`에 추가한다. 응답 30초 규칙으로 기본 60초 안에 들어와야 하며, 값을 올리는 것은 안전 여유다.

**안내문은 한 원본에서 생성한다**

```text
agent-docs/                  원본(사람이 작성)
  skill.md, workflows/, safety.md
레지스트리                   커맨드 참조(생성)
        ↓ dobim docs build
.claude/skills/<제품>/SKILL.md       name·description만 사용
.agents/skills/<제품>/SKILL.md       동일 내용
.agents/skills/<제품>/agents/openai.yaml   MCP 의존 선언(Codex 어댑터)
AGENTS.md                    원본
CLAUDE.md                    "@AGENTS.md" 한 줄 + Claude 고유 안내
```

두 클라이언트가 같은 Agent Skills 표준을 기반으로 하므로 공통 필드만 쓴다. Claude Code의 `context: fork` 같은 확장 필드는 쓰지 않는다. 참조 문서는 `references/`에 두어 필요할 때만 로드한다. CI에서 생성물과 저장본이 같은지 검사한다.

### 15.8 보안

| 위험 | 처리 |
|---|---|
| 파일 접근 | `--workspace`(기본: 서버 시작 위치) 안만 허용. Roots는 폐기 예정이라 쓰지 않음. 경로는 실제 경로로 해석해 `..`, 심볼릭 링크, UNC, 장치 이름을 검사. 벗어나면 `PATH_DENIED`. 추가 경로는 사용자가 `--allow-path`로 서버 시작 시 지정 |
| 사용자 스크립트 | 패밀리·IDS·템플릿은 코드 실행 없는 데이터. Python 플러그인은 `--allow-code`로 시작한 서버에서만 로드. 기본 꺼짐. 도구 호출로 켤 수 없음. `risk=code_exec`로 표시 |
| 파괴적 커맨드 | 실행 취소로 복구되지 않는 것만 `destructive`다(파일 덮어쓰기, `project.delete`, `history.purge`). 1회차 호출은 미리보기와 확인 토큰(인자 해시 + rev에 묶임, 5분 유효)을 반환하고, 2회차에 토큰으로 실행. CLI는 `--confirm <토큰>`. 실행 전 자동 스냅숏 |
| 요소 삭제 | `write`다. 커맨드 로그로 되돌릴 수 있기 때문이다 |
| 네트워크 | MVP는 stdio만. HTTP를 열 때는 127.0.0.1 + 토큰, 별도 결정 |
| 로그 | 파일 경로 외 사용자 데이터를 stderr에 과도하게 남기지 않음 |

"모든 기능" 요구와의 관계: `code_exec`는 켜야 쓸 수 있지만 양쪽 클라이언트에서 같은 방법으로 켜지고 같게 동작한다. 패리티 테스트는 이 플래그를 켠 상태로 실행한다.

### 15.9 헤드리스와 패키지

```text
dobim-core   라이브러리, CLI, MCP, 도면, CPU 렌더. GUI·GPU 의존 없음
dobim-gpu    선택. wgpu 오프스크린 경로
dobim-app    Tauri 앱. dobim-core에 의존
```

의존 방향은 `dobim-app → dobim-core`뿐이다. CI에서 다음으로 강제한다.

- import-linter(BSD)로 `core`가 `app`·GUI 라이브러리를 import하지 못하게 한다.
- 설치 메타데이터 트리에 GUI·디스플레이 패키지가 없는지 검사한다.
- Linux 휠에 GUI 시스템 라이브러리 링크가 없는지 `ldd`로 확인한다. `libGL` 등 예외가 나오면 원인을 기록한다.
- 디스플레이가 없는 컨테이너에서 RA-01을 완주한다(`roadmap.md` S10).

**렌더 경로**

| 산출물 | 경로 | GPU·디스플레이 |
|---|---|---|
| 도면 벡터 SVG·PDF·DXF | 코어의 벡터 경로 | 불필요 |
| 도면 PNG | SVG → resvg(Apache-2.0, 저장소 SPDX 확인) | 불필요 |
| 3D PNG 기본 | CPU 진단 렌더: 소프트웨어 z-버퍼, 평면 음영 + 특징선 + 요소 ID 버퍼. 후보는 S10에서 비교 | 불필요 |
| 3D PNG 선택 | wgpu 오프스크린(GPU 또는 lavapipe·WARP 소프트웨어 어댑터). 동작 범위는 S10 측정 | 있으면 사용 |

CPU 진단 렌더를 기본으로 두는 이유는 어디서나 돌고, 결과가 결정적이어서 골든 비교가 쉽기 때문이다. GUI 화면과 픽셀이 같지는 않다.

**기능별 헤드리스 제약**

| 기능 | 헤드리스 제약 |
|---|---|
| 모델링, 조회, 일람표, 패밀리, 이력, 설정 | 없음 |
| IFC 입출력, IDS·한국 규칙 검사, 간섭 | 없음 |
| 도면·시트 SVG·PDF·DXF | 없음. 한글 글꼴을 패키지에 동봉(OFL 계열, 선택은 S3) |
| 3D 이미지 | 기본은 재질·그림자·텍스처 없는 진단 품질. 사진 품질 렌더는 전체 범위 밖 |
| 대형 모델 렌더·도면 | 느림. 작업 핸들로 진행 |
| DWG | MVP 범위 밖. 선택 변환기 사용 시 그 도구의 약관과 디스플레이 요구를 따름(미확인) |
| 끌기 제스처, 카메라 궤도 | 없음. 15.2절의 커맨드로 대체 |
| `session.selection` | GUI가 붙어 있을 때만 의미 있음. 그 외는 `NO_GUI_ATTACHED` |

### 15.10 Windows 실행 환경

이 PC가 1차 환경이다. S11에서 전부 시험한다.

| 문제 | 대책 |
|---|---|
| stdout 오염. C++ 라이브러리(OCCT 등)의 printf도 MCP 스트림을 깨뜨림 | 시작 즉시 fd 1을 복제해 프로토콜 전용으로 보관하고, fd 1을 stderr로 돌림. Python `print`도 같이 처리 |
| 인코딩 | 프로토콜은 UTF-8 이진 스트림, 개행 `\n` 고정. `PYTHONUTF8=1`. 콘솔 cp949 영향 배제 |
| BOM | 입력 JSON은 `utf-8-sig`로 읽음. PowerShell 5.1 산출물 대응 |
| 한글·공백·긴 경로 | `pathlib`, 긴 경로 시험. 인자는 셸 문자열이 아닌 배열로 전달 |
| Python 실행 | `python`은 이 PC에서 작동하지 않는 Store stub. PATH에 의존하지 않고 `dobim.exe` 또는 `uvx.exe` 절대 경로로 실행 |
| 환경 변수 | Codex 설정의 기존 서버처럼 `SystemRoot` 전달 필요 여부 확인(이 PC `config.toml`에 선례) |
| 콘솔 창·고아 프로세스 | 자식 프로세스 `CREATE_NO_WINDOW`. stdin EOF에 종료. 작업 워커는 Job Object로 묶음 |
| 시작 시간 | 무거운 import 지연. `tools/list`는 ifcopenshell을 로드하지 않고 응답. 백신 영향 측정 |
| 파일 잠금 | 내보내기 대상이 열려 있으면(PDF 뷰어 등) 임시 파일 후 교체, 실패 시 `FILE_LOCKED` |
| 샌드박스 | Codex 샌드박스 영향 미확인. 측정 |
