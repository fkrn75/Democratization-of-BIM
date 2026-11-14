// 후보 A: ifc-lite (@ifc-lite/renderer + geometry, MPL-2.0) — WebGPU 렌더러, WASM 지오메트리(IFC 직접 파싱).
// 교체 = scene.removeMeshesForEntity(id) + renderer.addMeshes([새 MeshData]) + render.
import { Renderer } from '@ifc-lite/renderer';
import { GeometryProcessor, decodeInstancedShard } from '@ifc-lite/geometry';
import { measureFps, nextFrame, stats, mem } from './common.js';

// 24정점 박스 MeshData
const box = (id, cx, cy, cz, sx, sy, sz) => {
  const P = [], N = [], I = [];
  const hx = sx / 2, hy = sy / 2, hz = sz / 2;
  const quad = (n, p0, p1, p2, p3) => {
    const o = P.length / 3;
    for (const p of [p0, p1, p2, p3]) { P.push(cx + p[0], cy + p[1], cz + p[2]); N.push(...n); }
    I.push(o, o + 1, o + 2, o, o + 2, o + 3);
  };
  quad([0, 0, 1], [-hx, -hy, hz], [hx, -hy, hz], [hx, hy, hz], [-hx, hy, hz]);
  quad([0, 0, -1], [-hx, hy, -hz], [hx, hy, -hz], [hx, -hy, -hz], [-hx, -hy, -hz]);
  quad([1, 0, 0], [hx, -hy, -hz], [hx, hy, -hz], [hx, hy, hz], [hx, -hy, hz]);
  quad([-1, 0, 0], [-hx, -hy, hz], [-hx, hy, hz], [-hx, hy, -hz], [-hx, -hy, -hz]);
  quad([0, 1, 0], [-hx, hy, hz], [hx, hy, hz], [hx, hy, -hz], [-hx, hy, -hz]);
  quad([0, -1, 0], [-hx, -hy, -hz], [hx, -hy, -hz], [hx, -hy, hz], [-hx, -hy, hz]);
  return { expressId: id, positions: new Float32Array(P), normals: new Float32Array(N), indices: new Uint32Array(I), color: [0.8, 0.4, 0.3, 1] };
};

// GPU 없이 지오메트리(WASM) 스트리밍 시간만 측정하는 모드(CPU 전용)
window.runGeomOnly = async ({ base, instancing = false }) => {
  const t0 = performance.now();
  const buf = new Uint8Array(await fetch(`${base}.ifc`).then(r => r.arrayBuffer()));
  const t1 = performance.now(); const geometry = new GeometryProcessor({ enableInstancing: instancing }); await geometry.init(); const t2 = performance.now();
  let meshes = 0, tris = 0, first = null; const ids = new Set();
  const evs = {}; for await (const ev of geometry.processAdaptive(buf)) { evs[ev.type] = (evs[ev.type] || 0) + 1; if (ev.type === 'complete' || ev.type === 'error') evs[ev.type + '_info'] = JSON.stringify(ev).slice(0, 300); if (ev.type === 'batch') { if (first === null) first = performance.now() - t2; for (const m of ev.meshes) { meshes++; tris += m.indices.length / 3; ids.add(m.expressId); } } }
  const t3 = performance.now();
  return { candidate: 'ifc-lite(geometry only, CPU)', ifc_bytes: buf.byteLength, fetch_ms: +(t1 - t0).toFixed(0), init_ms: +(t2 - t1).toFixed(0), geometry_ms: +(t3 - t2).toFixed(0), first_batch_ms: +first.toFixed(0), events: evs, meshes, elements: ids.size, triangles: tris, ...mem() };
};

window.run = async ({ base, durMs = 5000, replaceN = 40, instancing = false }) => {
  const t0 = performance.now();
  const buf = new Uint8Array(await fetch(`${base}.ifc`).then(r => r.arrayBuffer()));
  const tFetch = performance.now();
  const canvas = document.getElementById('c');
  const renderer = new Renderer(canvas); const geometry = new GeometryProcessor({ enableInstancing: instancing });
  await Promise.all([renderer.init(), geometry.init()]);
  const tInit = performance.now();
  const meshes = []; const shards = []; let batches = 0, firstBatchMs = null;
  for await (const ev of geometry.processAdaptive(buf)) {
    if (ev.type === 'batch') {
      meshes.push(...ev.meshes); batches++; if (firstBatchMs === null) firstBatchMs = performance.now() - t0;
      if (ev.instancedShards) for (const sb of ev.instancedShards) shards.push(decodeInstancedShard(sb));
    }
  }
  const tParse = performance.now();
  renderer.loadGeometry(meshes);
  const dev = renderer.getGPUDevice ? renderer.getGPUDevice() : null;
  if (shards.length) for (const sh of shards) renderer.getScene().addInstancedShard(dev, sh);
  renderer.fitToView(); renderer.render();
  const gpuDone = async () => { if (dev) await dev.queue.onSubmittedWorkDone(); };
  await gpuDone(); await nextFrame();
  const load_ms = performance.now() - t0;
  const idSet = new Set(meshes.map(m => m.expressId)); for (const sh of shards) for (const i of sh.instances) idSet.add(i.entityId);
  const ids = [...idSet]; if (!ids.length) throw new Error('요소 0개');
  const out = {
    candidate: 'ifc-lite', renderer_pkg: '@ifc-lite/renderer 7.1.0', geometry_pkg: '@ifc-lite/geometry 8.1.0', load_ms: +load_ms.toFixed(0),
    breakdown_ms: { fetch: +(tFetch - t0).toFixed(0), init: +(tInit - tFetch).toFixed(0), geometry_stream: +(tParse - tInit).toFixed(0), upload_first_frame: +(performance.now() - tParse).toFixed(0), first_batch_at: firstBatchMs === null ? null : +firstBatchMs.toFixed(0) },
    instancing, shards: shards.length, batches, mesh_count: meshes.length, instance_count: shards.reduce((a, sh) => a + sh.instances.length, 0), elements: ids.length, triangles: meshes.reduce((a, m) => a + m.indices.length / 3, 0), has_device_handle: !!dev,
  };
  const frame = () => { renderer.getCamera().orbit(3, 0, false); renderer.render(); };
  out.fps = await measureFps(frame, durMs);
  try { renderer.setGpuFrameTiming(true); } catch {}
  const g = []; for (let i = 0; i < 60; i++) { const s = performance.now(); frame(); await gpuDone(); g.push(performance.now() - s); }
  out.gpu_bound_frame_ms = stats(g.slice(5));
  try { out.gpu_timing_mode = renderer.getGpuFrameTiming().mode; out.frame_stats = renderer.getFrameStats(); } catch (e) { out.gpu_timing_err = String(e).slice(0, 200); }
  // 요소 교체
  const scene = renderer.getScene(); const rep = []; const core = []; let err = null;
  const rnd = (() => { let s = 4242; return () => (s = (s * 1664525 + 1013904223) >>> 0) / 4294967296; })();
  for (let k = 0; k < replaceN; k++) {
    const id = ids[Math.floor(rnd() * ids.length)];
    const t1 = performance.now();
    try {
      scene.removeMeshesForEntity(id);
      renderer.addMeshes([box(id, rnd() * 100, rnd() * 100, rnd() * 20, 0.5, 0.5, 3)]);
      renderer.render(); await gpuDone(); core.push(performance.now() - t1); await nextFrame();
    } catch (e) { err = String(e && e.stack || e).slice(0, 600); break; }
    rep.push(performance.now() - t1);
  }
  if (rep.length) { out.replace_ms = stats(rep); out.replace_core_ms = stats(core); } out.replace_core_ms = stats(core);
  if (err) out.replace_error = err;
  out.replace_note = 'scene.removeMeshesForEntity + renderer.addMeshes(새 박스) + render + GPU 완료 + 다음 rAF';
  Object.assign(out, mem());
  return out;
};
