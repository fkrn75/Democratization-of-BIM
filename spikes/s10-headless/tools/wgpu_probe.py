# SPDX-License-Identifier: MPL-2.0
"""후보 시험: wgpu(오프스크린) 로 RA-01 을 그려 본다. 선택 패키지(wgpu 는 코어에도 dobim_render 에도 넣지 않는다).

  python wgpu_probe.py <ifc> <out.png> [--fallback]   (--fallback = 소프트웨어 어댑터: WARP / lavapipe)
GPU 경로는 float32 라 CPU 래스터라이저와 픽셀이 같지 않다. 얼마나 다른지도 잰다(요소 ID 경계 제외한 면 색 일치율).
stdout 은 JSON 한 줄.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dobim_render import mesh as M, png, raster  # noqa: E402

SHADER = """
struct VOut { @builtin(position) pos: vec4f, @location(0) col: vec3f };
@vertex fn vs(@location(0) p: vec3f, @location(1) c: vec3f) -> VOut {
  var o: VOut; o.pos = vec4f(p, 1.0); o.col = c; return o;
}
@fragment fn fs(i: VOut) -> @location(0) vec4f { return vec4f(i.col, 1.0); }
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ifc")
    ap.add_argument("out")
    ap.add_argument("--fallback", action="store_true")
    ap.add_argument("--width", type=int, default=1280)
    a = ap.parse_args()
    import wgpu

    t0 = time.perf_counter()
    m = M.extract(a.ifc)
    W = a.width
    H = (W * 5) // 8
    r, u, e = raster.camera_basis("iso")
    v = m.verts
    x, y, z = raster.dot3(v, r), raster.dot3(v, u), raster.dot3(v, e)
    margin = 24
    scale = min((W - 2 * margin) / (x.max() - x.min()), (H - 2 * margin) / (y.max() - y.min()))
    cx = (W - (x.max() - x.min()) * scale) / 2 - x.min() * scale
    cy = (H - (y.max() - y.min()) * scale) / 2 + y.max() * scale
    sx, sy = x * scale + cx, cy - y * scale
    zr = float(z.max() - z.min())
    ndc = np.stack([sx / W * 2 - 1, 1 - sy / H * 2, 1.0 - (z - z.min()) / zr * 0.98 - 0.01], axis=1).astype(np.float32)
    normals = raster.face_normals(v, m.faces)
    nv = normals * np.where(raster.dot3(normals, e) < 0, -1.0, 1.0)[:, None]
    lam = np.clip(raster.dot3(nv, raster._norm3(*raster.LIGHT)), 0, 1)
    shade = np.floor((0.45 + 0.55 * lam) * 256.0).astype(np.int32).clip(0, 256)
    base = np.array([raster.PALETTE.get(c, raster.DEFAULT_COLOR) for _, c in m.elems], np.int32)
    col = ((base[m.face_elem] * shade[:, None]) >> 8).astype(np.float32) / 255.0
    tri = ndc[m.faces].reshape(-1, 3)
    cols = np.repeat(col, 3, axis=0)
    vb = np.concatenate([tri, cols], axis=1).astype(np.float32)
    t1 = time.perf_counter()

    adapter = wgpu.gpu.request_adapter_sync(force_fallback_adapter=a.fallback)
    summary = adapter.summary
    dev = adapter.request_device_sync()
    mod = dev.create_shader_module(code=SHADER)
    fmt = wgpu.TextureFormat.rgba8unorm
    pipe = dev.create_render_pipeline(
        layout="auto",
        vertex={"module": mod, "entry_point": "vs", "buffers": [{
            "array_stride": 24, "attributes": [
                {"format": "float32x3", "offset": 0, "shader_location": 0},
                {"format": "float32x3", "offset": 12, "shader_location": 1}]}]},
        fragment={"module": mod, "entry_point": "fs", "targets": [{"format": fmt}]},
        primitive={"topology": "triangle-list", "cull_mode": "none"},
        depth_stencil={"format": "depth32float", "depth_write_enabled": True, "depth_compare": "less"})
    vbuf = dev.create_buffer_with_data(data=vb, usage=wgpu.BufferUsage.VERTEX)
    tex = dev.create_texture(size=(W, H, 1), format=fmt, usage=wgpu.TextureUsage.RENDER_ATTACHMENT | wgpu.TextureUsage.COPY_SRC)
    dtex = dev.create_texture(size=(W, H, 1), format="depth32float", usage=wgpu.TextureUsage.RENDER_ATTACHMENT)
    bpr = W * 4  # 1280*4 = 5120 은 256 의 배수
    rb = dev.create_buffer(size=bpr * H, usage=wgpu.BufferUsage.COPY_DST | wgpu.BufferUsage.MAP_READ)
    enc = dev.create_command_encoder()
    rp = enc.begin_render_pass(
        color_attachments=[{"view": tex.create_view(), "load_op": "clear", "store_op": "store", "clear_value": (245 / 255,) * 3 + (1,)}],
        depth_stencil_attachment={"view": dtex.create_view(), "depth_clear_value": 1.0, "depth_load_op": "clear", "depth_store_op": "store"})
    rp.set_pipeline(pipe)
    rp.set_vertex_buffer(0, vbuf)
    rp.draw(len(tri))
    rp.end()
    enc.copy_texture_to_buffer({"texture": tex}, {"buffer": rb, "bytes_per_row": bpr, "rows_per_image": H}, (W, H, 1))
    dev.queue.submit([enc.finish()])
    rb.map_sync(wgpu.MapMode.READ)
    img = np.frombuffer(rb.read_mapped(), np.uint8).reshape(H, W, 4)[:, :, :3].copy()
    rb.unmap()
    t2 = time.perf_counter()
    Path(a.out).write_bytes(png.encode_rgb(img))

    cpu = raster.render(m.verts, m.faces, m.face_elem, [c for _, c in m.elems], width=W)
    same = float((np.abs(img.astype(int) - cpu.color.astype(int)).max(axis=2) <= 3).mean())
    print(json.dumps({"adapter": summary, "fallback": a.fallback, "triangles": int(len(m.faces)),
                      "seconds": {"mesh": round(t1 - t0, 3), "gpu_total": round(t2 - t1, 3)},
                      "pixel_sha256": hashlib.sha256(img.tobytes()).hexdigest(),
                      "match_cpu_renderer_within3": round(same, 4)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
