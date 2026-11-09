// E1: W-E-01 의 배치 원점(IfcCartesianPoint #126)을 +Y 0.6m 로 옮긴다 (저수준 위치 속성 편집).
const w = bim.query().byType('IfcWall').toArray().find(x => x.name === 'W-E-01');
const pt = { modelId: w.ref.modelId, expressId: 126 };
bim.store.setPositionalAttribute(pt, 0, [0, 0.6, 0]);
const s = bim.export.ifc();
console.log('<<<IFC\n' + s + '\nIFC>>>');
