
// E2: 문 타입 DT-01 폭 0.9->1.0 (타입 Pset 값, 인스턴스 OverallWidth, 문·개구부 프로파일 XDim)
const M = 'default';
const set = (id, idx, v) => bim.store.setPositionalAttribute({ modelId: M, expressId: id }, idx, v);
// IfcPropertySingleValue.NominalValue 는 인덱스 2
set(90, 2, 1.0);
for (const id of [608, 834]) set(id, 9, 1.0);   // IfcDoor.OverallWidth (IFC4 인덱스 9)
for (const id of [617, 593, 843, 819]) set(id, 3, 1.0);                      // IfcRectangleProfileDef.XDim
console.log('<<<IFC\n' + bim.export.ifc() + '\nIFC>>>');
