# SPDX-License-Identifier: MPL-2.0
"""후보 시험: VTK(OSMesa) 오프스크린. 선택 패키지. python vtk_probe.py <ifc> <out.png>  — stdout JSON 한 줄."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dobim_render import mesh as M, png, raster  # noqa: E402


def main() -> int:
    import vtk
    from vtk.util import numpy_support as ns
    t0 = time.perf_counter()
    m = M.extract(sys.argv[1])
    W, H = 1280, 800
    pts = vtk.vtkPoints()
    pts.SetData(ns.numpy_to_vtk(m.verts.astype(np.float64), deep=True))
    cells = np.concatenate([np.full((len(m.faces), 1), 3, np.int64), m.faces.astype(np.int64)], axis=1).ravel()
    ca = vtk.vtkCellArray()
    ca.SetCells(len(m.faces), ns.numpy_to_vtkIdTypeArray(cells, deep=True))
    pd = vtk.vtkPolyData()
    pd.SetPoints(pts)
    pd.SetPolys(ca)
    base = np.array([raster.PALETTE.get(c, raster.DEFAULT_COLOR) for _, c in m.elems], np.uint8)
    arr = ns.numpy_to_vtk(base[m.face_elem], deep=True)
    arr.SetName("rgb")
    pd.GetCellData().SetScalars(arr)
    mp = vtk.vtkPolyDataMapper()
    mp.SetInputData(pd)
    mp.SetScalarModeToUseCellData()
    mp.SetColorModeToDirectScalars()
    ac = vtk.vtkActor()
    ac.SetMapper(mp)
    ac.GetProperty().SetInterpolationToFlat()
    ren = vtk.vtkRenderer()
    ren.AddActor(ac)
    ren.SetBackground(245 / 255, 245 / 255, 245 / 255)
    win = vtk.vtkRenderWindow()
    win.SetOffScreenRendering(1)
    win.SetMultiSamples(0)
    win.SetSize(W, H)
    win.AddRenderer(ren)
    cam = ren.GetActiveCamera()
    cam.ParallelProjectionOn()
    _, _, e = raster.camera_basis("iso")
    c = m.verts.mean(axis=0)
    cam.SetFocalPoint(*c)
    cam.SetPosition(*(c + np.array(e) * 100))
    cam.SetViewUp(0, 0, 1)
    ren.ResetCamera()
    t1 = time.perf_counter()
    win.Render()
    w2i = vtk.vtkWindowToImageFilter()
    w2i.SetInput(win)
    w2i.ReadFrontBufferOff()
    w2i.Update()
    img = ns.vtk_to_numpy(w2i.GetOutput().GetPointData().GetScalars()).reshape(H, W, -1)[::-1, :, :3].copy()
    t2 = time.perf_counter()
    Path(sys.argv[2]).write_bytes(png.encode_rgb(img))
    print(json.dumps({"vtk": vtk.vtkVersion.GetVTKVersion(), "render_window": win.GetClassName(), "opengl": win.ReportCapabilities().splitlines()[:4],
                      "seconds": {"mesh": round(t1 - t0, 3), "render": round(t2 - t1, 3)},
                      "pixel_sha256": hashlib.sha256(img.tobytes()).hexdigest()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
