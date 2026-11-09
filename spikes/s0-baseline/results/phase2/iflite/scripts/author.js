// store 네임스페이스(저작 API)로 기존 모델(RA-01)에 벽·문·창·기둥·보를 추가하고 내보낸다.
const log = [];
function T(label, fn) {
  try { const r = fn(); log.push(label + ' OK ' + JSON.stringify(r).slice(0, 60)); return r; } catch (e) { log.push(label + ' ERR ' + String(e.message).slice(0, 200)); return null; }
}
const st = bim.query().byType('IfcBuildingStorey').toArray().find(x => x.name === '1F');
const m = st.ref.modelId, sid = st.ref.expressId;
const w = T('addWall', () => bim.store.addWall(m, sid, { Start: [0, 12, 0], End: [6, 12, 0], Thickness: 0.2, Height: 3.3, Name: 'NEW-W1' }));
const w2 = T('addWall2', () => bim.store.addWall(m, sid, { Start: [6, 12, 0], End: [6, 14, 0], Thickness: 0.2, Height: 3.3, Name: 'NEW-W2' }));
if (w) {
  T('addHostedDoor', () => bim.store.addHostedDoor(m, w.expressId, { Offset: 2, Width: 0.9, Height: 2.1, Name: 'NEW-D1' }));
  T('addHostedWindow', () => bim.store.addHostedWindow(m, w.expressId, { Offset: 4.5, Width: 1.0, Height: 1.2, Sill: 0.9, Name: 'NEW-WIN1' }));
}
if (w && w2) T('joinWalls', () => bim.store.joinWalls(m, w.expressId, w2.expressId, {}));
T('addColumn', () => bim.store.addColumn(m, sid, { Position: [3, 13, 0], Width: 0.4, Depth: 0.4, Height: 3.3, Name: 'NEW-C1' }));
T('addBeam', () => bim.store.addBeam(m, sid, { Start: [0, 12, 3.0], End: [6, 12, 3.0], Width: 0.3, Height: 0.4, Name: 'NEW-B1' }));
T('addSlab', () => bim.store.addSlab(m, sid, { Position: [0, 12, -0.2], Width: 6, Depth: 2, Thickness: 0.2, Name: 'NEW-S1' }));
console.log('LOG ' + JSON.stringify(log));
console.log('<<<IFC\n' + bim.export.ifc() + '\nIFC>>>');
