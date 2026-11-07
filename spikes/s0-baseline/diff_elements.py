"""편집 전후 비교 (E1~E3 관찰용): 세계좌표 bbox 중심 이동, 체적 변화, 개구부/채움 관계 보존을 이름으로 나열한다.

사용법: python diff_elements.py BEFORE.ifc AFTER.ifc
"""
import sys

import ifcopenshell

import roundtrip_check as rc


def names(f):
    return {e.GlobalId: (e.Name, e.is_a()) for e in f.by_type("IfcProduct")}


def main(a, b):
    fa, fb = ifcopenshell.open(a), ifcopenshell.open(b)
    ea, eb = rc.extract(fa), rc.extract(fb)
    nm = names(fa)
    moved, resized, lost = [], [], []
    for g, c in ea["center"].items():
        t = eb["center"].get(g)
        if t is None:
            lost.append(nm[g][0])
            continue
        d = tuple(round(y - x, 4) for x, y in zip(c, t))
        if any(abs(v) > 1e-6 for v in d):
            moved.append((nm[g][0], d))
        va, vb = ea["volume"][g], eb["volume"][g]
        if abs(va - vb) > 1e-6:
            resized.append((nm[g][0], round(va, 4), round(vb, 4)))
    print("이동한 요소 %d개 / 형상 있는 요소 %d개" % (len(moved), len(ea["center"])))
    for n, d in sorted(moved):
        print("  이동", n, d)
    for n, x, y in sorted(resized):
        print("  체적변화", n, x, "->", y)
    if lost:
        print("  형상 소실:", lost)
    print("관계 보존: voids", ea["voids"] == eb["voids"], "fills", ea["fills"] == eb["fills"])
    va, vb = rc.validity(fa), rc.validity(fb)
    print("유효성 오류 전/후:", va["errors"], vb["errors"], vb["samples"][:2])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
