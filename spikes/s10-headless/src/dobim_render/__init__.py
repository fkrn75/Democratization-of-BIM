# SPDX-License-Identifier: MPL-2.0
"""S10 CPU 진단 렌더 (선택 패키지).

dobim_core 와 분리된 패키지다: 코어가 이 패키지를 import 하지 못한다(import-linter 계약).
이 패키지는 IFC 파일만 입력으로 받고 dobim_core 를 import 하지 않는다.
의존: numpy(BSD-3) + ifcopenshell(LGPL-3.0, 삼각형 추출 전용 — raster/png 는 ifcopenshell 을 모른다).
"""
__all__ = ["mesh", "raster", "png", "scene"]
