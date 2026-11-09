const c = ['project','toIfc','addStorey','storey','addBuildingStorey','addIfcStorey','addWall','wall','addElement','addAxisElement','addIfcElement','createProfile','getWorldPlacementId'];
console.log('CREATE ' + c.map(k => k + ':' + typeof bim.create[k]).join(' '));
const s = ['addWall','addColumn','addSlab','addBeam','addSpace','addHostedDoor','addHostedWindow','addElementType','assignType','addMaterial','addMaterialLayerSet','addMaterialLayerSetUsage','assignMaterial','addEntity','joinWalls','addOpening','addRoof','addPlate','addMember'];
console.log('STORE ' + s.map(k => k + ':' + typeof bim.store[k]).join(' '));
console.log('MODEL ' + ['list','active','activeId','loadIfc'].map(k => k + ':' + typeof bim.model[k]).join(' '));
console.log(JSON.stringify(bim.model.list()).slice(0, 200));
