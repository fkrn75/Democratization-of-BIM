// ifc-lite SDK(create 네임스페이스)로 RA-01 사양을 네이티브 생성. 호출마다 try/catch 로 매개변수 이름 오류를 기록한다.
const log = [];
function T(label, fn) {
  try { const r = fn(); log.push(label + ' OK'); return r; } catch (e) { log.push(label + ' ERR ' + String(e.message).slice(0, 160)); return null; }
}
const EXT_T = 0.32, INT_T = 0.10, H = 3.3, hi = EXT_T / 2;
const h = T('project', () => bim.create.project({ Name: 'RA-01' }));
const st1 = T('storey1', () => bim.create.addIfcBuildingStorey(h, { Name: '1F', Elevation: 0 }));
const st2 = T('storey2', () => bim.create.addIfcBuildingStorey(h, { Name: '2F', Elevation: 3.3 }));
const walls = [];
for (const [st, sn, z] of [[st1, '1F', 0], [st2, '2F', 3.3]]) {
  let k = 0;
  for (const [a, b] of [[[0, 0], [12, 0]], [[12, 0], [12, 8]], [[12, 8], [0, 8]], [[0, 8], [0, 0]]]) {
    k++;
    walls.push(T('wallE' + sn + k, () => bim.create.addIfcWall(h, st, { Start: [a[0], a[1], z], End: [b[0], b[1], z], Thickness: EXT_T, Height: H, Name: 'W-E-' + sn + k })));
  }
  for (const [a, b] of [[[4, hi], [4, 8 - hi]], [[4.05, 4], [12 - hi, 4]], [[8, hi], [8, 2]]]) {
    k++;
    walls.push(T('wallI' + sn + k, () => bim.create.addIfcWall(h, st, { Start: [a[0], a[1], z], End: [b[0], b[1], z], Thickness: INT_T, Height: H, Name: 'W-I-' + sn + k })));
  }
}
const d1 = T('door', () => bim.create.addIfcWallDoor(h, walls[0], { Position: [8, 0, 0], Width: 0.9, Height: 2.1, Name: 'D-01' }));
const w1 = T('window', () => bim.create.addIfcWindow ? 'has addIfcWindow' : 'no');
const col = T('column', () => bim.create.addIfcColumn(h, st1, { Position: [hi, hi, 0], Width: 0.4, Depth: 0.4, Height: H, Name: 'C-01' }));
const slab = T('slab', () => bim.create.addIfcSlab(h, st1, { Position: [0, 0, -0.2], Width: 12, Depth: 8, Thickness: 0.2, Name: 'S-01' }));
const beam = T('beam', () => bim.create.addIfcBeam(h, st2, { Start: [hi, hi, 3.1], End: [6, hi, 3.1], Width: 0.3, Height: 0.4, Name: 'B-01' }));
const space = T('space', () => bim.create.addIfcSpace(h, st1, { Position: [hi, hi, 0], Width: 3.8, Depth: 7.7, Height: H, Name: 'SP-101' }));
const stair = T('stair', () => bim.create.addIfcStair(h, st1, { Position: [1, 1, 0], NumberOfRisers: 18, RiserHeight: 0.1833, Width: 1.2, Name: 'ST-01' }));
const ps = T('pset', () => bim.create.addIfcPropertySet(h, walls[0], { Name: 'Pset_WallCommon', Properties: [{ Name: 'IsExternal', NominalValue: true }] }));
console.log('LOG ' + JSON.stringify(log));
const out = T('toIfc', () => bim.create.toIfc(h));
if (out) console.log('<<<IFC\n' + out.content + '\nIFC>>>');
