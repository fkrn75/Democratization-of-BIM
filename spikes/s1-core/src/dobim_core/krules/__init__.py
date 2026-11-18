# SPDX-License-Identifier: MPL-2.0
"""한국 납품 규칙 검사(S9 스파이크에서 옮김): IDS 18개 + 코드 19개 = 37개, 프로파일 procurement(기본)·seumteo(초안).

이 패키지의 runner·codechecks·slots·seumteo·ids_eval 은 ifcopenshell 을 쓴다. catalog·profiles 는 순수 파이썬이라
rules.list 처럼 모델 없이 규칙 목록만 볼 때는 ifcopenshell 을 import 하지 않는다. 이 __init__ 도 아무것도 import 하지 않는다.
"""
