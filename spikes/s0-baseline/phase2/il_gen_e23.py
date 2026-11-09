"""ifc-lite E2/E3 스크립트 생성: ID 조회에만 호스트 ifcopenshell 로 RA-01 을 읽는다 (편집은 ifc-lite SDK 가 수행)."""
import ifcopenshell
NL = "\n"  # JS 문자열 안의 줄바꿈 이스케이프
f = ifcopenshell.open("RA-01.ifc")
named = lambda n: next(e for e in f.by_type("IfcRoot") if getattr(e, "Name", None) == n)
dt01 = named("DT-01")
tps = next(p for p in dt01.HasPropertySets if p.Name == "DoBIM_TypeParams")
width_prop = next(p for p in tps.HasProperties if p.Name == "OverallWidth")
doors = [d for d in f.by_type("IfcDoor") if d.IsTypedBy and d.IsTypedBy[0].RelatingType.Name == "DT-01"]
prof = []
for d in doors:
    prof.append(d.Representation.Representations[0].Items[0].SweptArea.id())
    prof.append(d.FillsVoids[0].RelatingOpeningElement.Representation.Representations[0].Items[0].SweptArea.id())
e2 = f"""
// E2: 문 타입 DT-01 폭 0.9->1.0 (타입 Pset 값, 인스턴스 OverallWidth, 문·개구부 프로파일 XDim)
const M = 'default';
const set = (id, idx, v) => bim.store.setPositionalAttribute({{ modelId: M, expressId: id }}, idx, v);
// IfcPropertySingleValue.NominalValue 는 인덱스 2
set({width_prop.id()}, 2, 1.0);
for (const id of {[d.id() for d in doors]}) set(id, 9, 1.0);   // IfcDoor.OverallWidth (IFC4 인덱스 9)
for (const id of {prof}) set(id, 3, 1.0);                      // IfcRectangleProfileDef.XDim
console.log('<<<IFC@@' + bim.export.ifc() + '@@IFC>>>');
"""
open("out/p2/iflite/scripts/e2.js", "w", encoding="utf-8").write(e2.replace("@@", chr(92)+"n"))
st1, st2 = named("1F"), named("2F")
gf = [e for rel in st1.ContainsElements for e in rel.RelatedElements if e.is_a() in ("IfcWall", "IfcColumn")]
solids = [e.Representation.Representations[0].Items[0].id() for e in gf]
pt2 = st2.ObjectPlacement.RelativePlacement.Location.id()
e3 = f"""
// E3: 2F 층고 3.3->3.6: 2F Elevation(인덱스 9), 2F 배치 z, 1F 벽·기둥 압출 깊이
const M = 'default';
const set = (id, idx, v) => bim.store.setPositionalAttribute({{ modelId: M, expressId: id }}, idx, v);
set({st2.id()}, 9, 3.6);
set({pt2}, 0, [0, 0, 3.6]);
for (const id of {solids}) set(id, 3, 3.6);
console.log('<<<IFC@@' + bim.export.ifc() + '@@IFC>>>');
"""
open("out/p2/iflite/scripts/e3.js", "w", encoding="utf-8").write(e3.replace("@@", chr(92)+"n"))
print(len(doors), len(prof), len(solids), width_prop.id(), pt2)
