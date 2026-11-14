"""합성 IFC 생성기: N개 요소(기둥·보·벽·슬래브 타일)를 격자 건물로 만든다.
사용: python gen_ifc.py <요소수> <출력.ifc>
공개 표본 없이 순수 합성. 요소마다 고유 ExtrudedAreaSolid(직사각 프로파일)."""
import sys, random
import ifcopenshell
import ifcopenshell.guid as G

n_target = int(sys.argv[1]); out = sys.argv[2]
f = ifcopenshell.file(schema="IFC4")
O = f.createIfcCartesianPoint((0.0, 0.0, 0.0))
Z = f.createIfcDirection((0.0, 0.0, 1.0))
X = f.createIfcDirection((1.0, 0.0, 0.0))
w3 = f.createIfcAxis2Placement3D(O, Z, X)
ctx = f.createIfcGeometricRepresentationContext(None, "Model", 3, 1e-5, w3)
body = f.createIfcGeometricRepresentationSubContext("Body", "Model", None, None, None, None, ctx, None, "MODEL_VIEW", None)
ua = f.createIfcSIUnit(None, "LENGTHUNIT", None, "METRE")
proj = f.createIfcProject(G.new(), None, "S6", None, None, None, None, [ctx], f.createIfcUnitAssignment([ua]))
site = f.createIfcSite(G.new(), None, "Site", None, None, f.createIfcLocalPlacement(None, w3))
bld = f.createIfcBuilding(G.new(), None, "B", None, None, f.createIfcLocalPlacement(site.ObjectPlacement, w3))
f.createIfcRelAggregates(G.new(), None, None, None, proj, [site])
f.createIfcRelAggregates(G.new(), None, None, None, site, [bld])
Zd = Z
def box(lx, ly, h, x, y, z, cls, storey_pl, name):
    pl = f.createIfcLocalPlacement(storey_pl, f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((x, y, z)), Z, X))
    prof = f.createIfcRectangleProfileDef("AREA", None, f.createIfcAxis2Placement2D(f.createIfcCartesianPoint((0.0, 0.0)), None), lx, ly)
    sol = f.createIfcExtrudedAreaSolid(prof, w3, Zd, h)
    rep = f.createIfcShapeRepresentation(body, "Body", "SweptSolid", [sol])
    return f.create_entity(cls, G.new(), None, name, None, None, pl, f.createIfcProductDefinitionShape(None, None, [rep]))

per_storey = 2000
n_storeys = (n_target + per_storey - 1) // per_storey
made = 0
for s in range(n_storeys):
    z0 = s * 3.2
    spl = f.createIfcLocalPlacement(bld.ObjectPlacement, f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, z0)), Z, X))
    st = f.createIfcBuildingStorey(G.new(), None, f"L{s}", None, None, spl, None, None, "ELEMENT", z0)
    f.createIfcRelAggregates(G.new(), None, None, None, bld, [st])
    els = []
    # 격자 25x16: 기둥 400, 보 x방향/y방향, 벽 일부, 슬래브 타일 400
    nx, ny = 25, 16
    for i in range(nx):
        for j in range(ny):
            if made >= n_target: break
            els.append(box(0.4, 0.4, 3.0, i*6, j*6, 0, "IfcColumn", spl, f"C{made}")); made += 1
            if made >= n_target: break
            els.append(box(6.0, 0.3, 0.5, i*6, j*6, 2.7, "IfcBeam", spl, f"Bx{made}")); made += 1
            if made >= n_target: break
            els.append(box(0.3, 6.0, 0.5, i*6, j*6, 2.7, "IfcBeam", spl, f"By{made}")); made += 1
            if made >= n_target: break
            els.append(box(5.5, 0.2, 2.6, i*6+0.25, j*6+3.0, 0.1, "IfcWall", spl, f"W{made}")); made += 1
            if made >= n_target: break
            els.append(box(5.5, 5.5, 0.2, i*6+0.25, j*6+0.25, 3.0, "IfcSlab", spl, f"S{made}")); made += 1
    if els:
        f.createIfcRelContainedInSpatialStructure(G.new(), None, None, None, els, st)
f.write(out)
print("elements", made, "storeys", n_storeys)
