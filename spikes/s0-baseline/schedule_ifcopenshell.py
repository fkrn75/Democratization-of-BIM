"""E6 관찰: 문 일람표를 ifcopenshell.util.selector 로 만든다 (CLI 에는 일람표 명령이 없어 라이브러리 사용).
사용: python schedule_ifcopenshell.py IN.ifc"""
import sys

import ifcopenshell
import ifcopenshell.util.selector as sel

f = ifcopenshell.open(sys.argv[1])
cols = ["Name", "type.Name", "OverallWidth", "Pset_DoorCommon.FireRating", "type.DoBIM_TypeParams.OverallWidth"]
print(" | ".join(cols))
for d in sorted(sel.filter_elements(f, "IfcDoor"), key=lambda e: e.Name):
    print(" | ".join(str(sel.get_element_value(d, c)) for c in cols))
