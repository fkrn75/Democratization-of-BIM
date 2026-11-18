# 외래 IFC 도면 코퍼스 출처표 (1단계, 2026-11-18)

1단계 완료 기준 "표본 5건(소형 건물·Revit 내보내기 포함)에서 재생성 차등 테스트 통과"에 쓰는 표본이다. 도구는 `../foreign_corpus.py`, 시험은 `spikes/s1-core/tests/test_foreign_drawings.py`, 기준선은 `baseline.json`(저장소 표본의 뷰별 선분·SVG 해시)이다.

| id | 파일 | 만든 도구 | 출처 | 라이선스 | 저장소 포함 | sha256 |
|---|---|---|---|---|---|---|
| BA | `corpus/Building-Architecture.ifc` (IFC4, 142,325 B) | SketchUp IFC Manager | buildingSMART `Certification-datasets`(옛 이름 Sample-Test-Files) 커밋 `80d976a9b193a26a8e928c3e79bff67af1de68a8`, 경로 `IFC 4.0.2.1 (IFC 4 ADD2 TC1)/Simple-Scene/Building-Architecture.ifc`, https://github.com/buildingSMART/Certification-datasets | CC-BY-4.0, (C) buildingSMART International Ltd. (저장소 LICENSE 파일) | 예 | `8790a1e193e82b8e7e7f337ec2633cd40f2120590317a1443503a25b079e2e80` |
| BS | `corpus/Building-Structural.ifc` (IFC4, 185,185 B) | SketchUp IFC Manager | 같은 저장소·커밋, 경로 `IFC 4.0.2.1 (IFC 4 ADD2 TC1)/Simple-Scene/Building-Structural.ifc` | CC-BY-4.0, (C) buildingSMART International Ltd. | 예 | `903b5a005901397aa2b235daf6a60f46c099eae286cb01c815abf0a341707432` |
| RA01 | `spikes/s0-baseline/RA-01.ifc` (IFC4) | 이 저장소 `spikes/s0-baseline/build_ra01.py`(ifcopenshell.api) | 이 저장소 S0 산출물 | MPL-2.0(저장소 라이선스) | 예(기존 파일) | `49bf1c83db2e091c20430e694adda07e28b95cc64ca016a2f81243adfe32b969` |
| RA01-bonsai | `spikes/s0-baseline/results/bonsai/RA-01.bonsai.ifc` (IFC4) | Bonsai 0.9.0-post1 (RA-01 을 열어 저장) | 이 저장소 S0 산출물 | MPL-2.0(저장소 라이선스) | 예(기존 파일) | `80de25902b944416d6bba6769cd04e81f744dbf12a86c21889f4e22661f800cf` |
| RA01-revit | `RA-01_IFC4RV.ifc` (IFC4 Reference View) | Autodesk Revit 2025 IFC 내보내기 | 프로젝트 담당자의 Revit 시험 산출(비공개) | 회사 Revit 계정으로 만든 파일이라 공개 라이선스가 없다 | **아니오(로컬 전용)**: 환경 변수 `DOBIM_REVIT_DIR` 로만 참조. 기준선도 저장소 밖(`DOBIM_FOREIGN_LOCAL_BASELINE`) | 비공개 |

## 저작자 표시 (CC-BY-4.0)

`Building-Architecture.ifc`, `Building-Structural.ifc`: (C) buildingSMART International Ltd., Creative Commons Attribution 4.0 International(https://creativecommons.org/licenses/by/4.0/). 파일은 수정하지 않고 그대로 두었다(바이트 해시가 위 표와 같다).

## 넣지 않은 것과 이유

- buildingSMART 저장소는 GitHub 메타데이터상 라이선스가 `NOASSERTION` 이지만 저장소 루트 `LICENSE` 파일이 CC-BY-4.0 을 밝힌다. 그 파일을 근거로 했다.
- 실무 현장 모델 자료와 Revit 산출 파일(.rvt·내보낸 .ifc)은 저장소·외부에 넣지 않는다. 로컬에서 따로 돌린 측정 수치(시간·선분 수)만 baseline.md 에 적는다.
- 같은 저장소의 `Building-Hvac`·`Infra-*` 표본은 건축 도면 대상이 아니라 이번 코퍼스에서 뺐다.
