# S6 뷰어 스파이크 보고서

질문: 뷰어는 무엇을 쓰는가? 합격 기준(roadmap 2절): 5만 요소에서 RTX 4060 30fps 이상, 요소 1개 교체 100ms 이내, WebView2에서 동작. 후보는 ifc-lite, That Open Fragments, 직접 구현(three.js).

## 1. 측정 조건

- 모델: 합성 IFC4 두 개(ifcopenshell로 생성, `tools/gen_ifc.py`). 5만 요소(기둥·보·벽·슬래브, 25층, 60만 삼각형, 26MB)와 5천 요소(6만 삼각형). 요소마다 직사각 압출체라 실제 BIM보다 삼각형이 가볍다.
- 환경: RTX 4060 Laptop 8GB, headless Edge(Chromium 157.0.4341.46), 1280x800, 실 GPU(WebGL은 ANGLE D3D11, WebGPU는 nvidia/lovelace). 창은 띄우지 않았다. 측정 중 GPU 사용률은 다른 프로세스 때문에 6~41%였다(측정 직전 `nvidia-smi`). 다른 세션의 gpu.lock이 해제될 때까지 기다린 뒤 직접 잠금을 잡고 측정했다.
- fps: 카메라를 매 프레임 회전하며 5초 rAF 루프. **headless rAF는 60Hz로 막혀 있어 fps는 모두 60.0에서 포화**한다. 여유는 "GPU 바운드 프레임 시간"(렌더 직후 GPU 완료까지 대기)으로 본다. 1000/값이 대략의 상한 fps이다.
- 교체: 40회 무작위 요소 교체의 중앙값. "core"는 교체 호출부터 GPU 완료까지이고, 전체는 다음 rAF까지(vsync 대기 약 16.7ms 포함). 판정은 core 기준.

## 2. 결과(5만 요소)

| 후보 | 로드 | fps(rAF) | GPU 바운드 프레임 | 교체 1개(core 중앙/p95) | JS 힙 | 판정 |
|---|---|---|---|---|---|---|
| ifc-lite (WebGPU, flat) | 1.66s (WASM 지오메트리 0.93s + 업로드 0.58s) | 60 | 5.0ms | 46.7 / 70.2ms | 192MB | 통과 |
| ifc-lite (WebGPU, 인스턴싱) | 1.18s | 60 | 9.0ms | 7.3 / 9.0ms (주의 1) | 47MB | 통과(주의 1) |
| Fragments 3.6.1 (three WebGL) | 1.41s + 변환 7.5s(IFC→.frag, 2.5MB) | 60 | 2.3ms | 667 / 776ms | 20MB | 교체 불합격 |
| 직접 구현 three.js WebGL2 | 0.26s | 60 | 2.8ms | 3.7 / 5.2ms | 74MB | 통과(주의 2) |
| 직접 구현 three.js WebGPU | 0.41s | 60 | 4.6ms | 4.5 / 5.8ms | 75MB | 통과(주의 2) |
| 직접 구현, GPU 없음(SwiftShader) | 0.60s | 3.4 | 343ms | 322ms | 75MB | 폴백 불가 |

5천 요소: ifc-lite GPU 4.0ms·교체 9.2ms, Fragments 1.5ms·교체 258ms, 직접 1.5ms·3.5ms. Fragments 교체는 모델 크기에 따라 커진다(5천 258ms, 5만 667ms). 원자료는 `results/*.json`.

Fragments 교체 변형(5만): 삭제+생성+applyChanges 621ms, 삭제+생성 706ms, 기존 요소 변환만 편집(UPDATE_LOCAL_TRANSFORM) 649ms. 편집 요청 처리 자체가 약 550~600ms이고 강제 update는 15~60ms이다. 어느 변형도 100ms를 넘는다.

주의 1: 인스턴싱 모드는 반복 형상이 템플릿+인스턴스로 가서(108 샤드, 드로우콜 105) 교체 호출이 7ms로 나왔지만, 인스턴스 쪽 요소가 실제로 제거됐는지 시각 확인은 못 했다. 신뢰할 교체 수치는 flat 경로의 46.7ms다.
주의 2: 직접 구현은 요소 정점 범위를 제자리 덮어쓰는 최소 구현이다. 삼각형 수가 슬롯 이하일 때만 성립하고, 슬롯 초과 시 재할당·피킹·구간 컬링·LOD·재질은 만들지 않았다. 수치는 상한이지 제품 수준이 아니다.

## 3. WebView2

- 이 PC의 WebView2 Evergreen 런타임은 157.0.4341.46으로 측정에 쓴 Edge와 같은 Chromium 빌드다. 특수 플래그 없이 기본값으로 WebGL2(실 GPU), WebGPU 어댑터(nvidia/lovelace), SharedArrayBuffer가 모두 사용 가능했다(`results/webview2-probe.json`).
- 실제 WebView2 호스트(Tauri 창) 안에서의 시험은 하지 않았다. 창이 필요하므로 미측정으로 남긴다. Tauri 호스트에서의 COOP/COEP·GPU 선택은 확인이 필요하다.

## 4. 라이선스(MPL-2.0 프로젝트 호환성)

| 패키지 | 버전 | 라이선스 | 비고 |
|---|---|---|---|
| @ifc-lite/renderer·geometry·parser·data·wasm | 7.1.0·8.1.0·9.9.0·6.4.0·12.2.0 | MPL-2.0 | 파일 단위 카피레프트, 우리와 같은 라이선스라 호환 |
| @thatopen/fragments | 3.6.1 | MIT | package.json 기준(패키지에 LICENSE 파일 없음, 확인 필요) |
| web-ifc (Fragments 변환기 의존) | 0.0.79 | MPL-2.0 | |
| three | 0.187.0 | MIT | |

GPL 소스는 열람하지 않았다.

## 5. 후보별 한계

- **ifc-lite**: 렌더러가 WebGPU 전용이다. WebGPU 어댑터가 없으면 `Failed to get GPU adapter`로 실패하고 WebGL2 폴백이 없다(SwiftShader에서 확인). IFC를 직접 읽어 변환 단계가 없고 5만 요소 지오메트리 0.9s로 빠르다. 교체 API는 `removeMeshesForEntity` + `addMeshes`로 저수준이다. 패키지 릴리스가 매우 잦아 버전 고정이 필요하다.
- **Fragments**: 렌더링은 가볍고 메모리도 적다. 그러나 편집 경로가 5만 요소에서 약 0.65s로 기준(100ms)을 크게 넘는다. 읽기 전용 뷰어로는 적합하고 저작 뷰로는 부적합하다. IFC→.frag 변환(7.5s)이 별도로 필요하다.
- **직접 구현**: 가장 빠르지만 피킹·선택 강조·단면·재질·임의 형상 교체를 모두 우리가 만들어야 한다. 최소 구현이 기준을 크게 넘으므로 기준 자체는 어려운 목표가 아니다.

## 6. 판정과 권고

세 후보 모두 5만 요소 30fps를 통과한다(GPU 상한 약 110~430fps). 병목은 렌더링이 아니라 교체 지연이다.

- 권고: 1차 뷰어는 ifc-lite로 한다. 교체 47ms로 기준을 통과하고, MPL-2.0이라 라이선스가 맞으며, 변환 파이프라인이 필요 없다. 위험은 WebGPU 전용이라는 점이다.
- 폴백(WebGL2): 직접 구현 three.js(WebGL2/WebGPU 겸용, 교체 4ms)를 폴백 렌더러로 두고 요소 교체 계약(id, 정점 범위)을 공유한다. Fragments는 교체 기준에서 탈락했으므로 저작 뷰로 쓰지 않고 필요하면 읽기 전용 납품 뷰어로만 검토한다.
- 저작 단계는 교체 단위를 소규모로 유지한다(로드맵의 "저작 단계는 소규모만" 대안과 일치).

## 7. 못 한 것과 위험

- 실제 WebView2 호스트(Tauri) 안의 측정(창 필요).
- 실제 BIM(곡면·복잡 브렙, 수백만 삼각형) 표본. 합성 박스라 GPU 부하가 낮다. 공개 표본 IFC로 재측정이 필요하다.
- rAF 60Hz 상한으로 fps가 60에 포화했다. 상한 fps는 GPU 바운드 프레임 시간에서 추정(WebGL은 readPixels 동기화)한 값이다.
- ifc-lite 인스턴싱 경로의 교체 후 인스턴스 제거 확인, 피킹·선택 지연, 장시간 메모리 증가 미측정.
- 모바일·저사양 GPU 미측정.

## 8. 재현

```
cd spikes/s6-viewer && npm i
py -3 tools/gen_ifc.py 50000 model/m50k.ifc          # 합성 IFC (ifcopenshell 필요)
py -3 tools/export_mesh.py model/m50k.ifc model/m50k  # 직접 구현용 메쉬
node tools/ifc2frag.mjs model/m50k.ifc model/m50k.frag
node build.mjs direct fragments && npx vite build      # 번들(dist/)
cp node_modules/@thatopen/fragments/dist/Worker/worker.mjs dist/fragments-worker.mjs
node tools/run.mjs <direct|fragments|iflite> m50k '{"backend":"webgl"}'   # headless Edge, 결과는 results/
node tools/summarize.mjs
```
GPU 측정 전에는 `.resource-locks/gpu.lock` 규약을 따른다.
