
// E3: 2F 층고 3.3->3.6: 2F Elevation(인덱스 9), 2F 배치 z, 1F 벽·기둥 압출 깊이
const M = 'default';
const set = (id, idx, v) => bim.store.setPositionalAttribute({ modelId: M, expressId: id }, idx, v);
set(15, 9, 3.6);
set(64, 0, [0, 0, 3.6]);
for (const id of [189, 463, 1437, 119, 394, 1521, 1549, 1465, 155, 429, 1409, 1493, 223]) set(id, 3, 3.6);
console.log('<<<IFC\n' + bim.export.ifc() + '\nIFC>>>');
