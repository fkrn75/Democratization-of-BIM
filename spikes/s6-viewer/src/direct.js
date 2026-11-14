// 후보 C: 직접 구현 — three.js 병합 BufferGeometry(요소별 정점 범위) + 정점색 셰이딩.
// 교체 = 요소의 정점 범위를 제자리 덮어쓰기(삼각형 수가 슬롯 이하일 때). 증분 GPU 업로드(updateRanges).
import * as THREE from 'three';
import { measureFps, nextFrame, stats, mem } from './common.js';

window.run = async ({ base, backend, durMs = 5000 }) => {
  const t0 = performance.now();
  const [meta, pos, nor] = await Promise.all([
    fetch(`${base}.json`).then(r => r.json()),
    fetch(`${base}.pos`).then(r => r.arrayBuffer()), fetch(`${base}.nor`).then(r => r.arrayBuffer())]);
  const tFetch = performance.now();
  const canvas = document.getElementById('c');
  let renderer, gpuDone, Th = THREE;
  if (backend === 'webgpu') {
    Th = await import('three/webgpu');
    renderer = new Th.WebGPURenderer({ canvas, antialias: true }); await renderer.init();
    gpuDone = () => renderer.backend.device.queue.onSubmittedWorkDone();
  } else {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    const gl = renderer.getContext(); const px = new Uint8Array(4); gpuDone = async () => gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  }
  renderer.setSize(1280, 800, false);
  const scene = new Th.Scene(); scene.background = new Th.Color(0xdde3ea);
  const geo = new Th.BufferGeometry();
  const pa = new Th.BufferAttribute(new Float32Array(pos), 3), na = new Th.BufferAttribute(new Float32Array(nor), 3);
  geo.setAttribute('position', pa); geo.setAttribute('normal', na);
  geo.computeBoundingSphere();
  const col = new Float32Array(meta.vertices * 3); // 유형별 정점색
  const pal = { IfcColumn: [.55, .55, .6], IfcBeam: [.7, .5, .3], IfcWall: [.85, .85, .8], IfcSlab: [.6, .7, .8] };
  meta.ranges.forEach(([id, s, c], i) => { const k = pal[meta.types[i]] || [.7, .7, .7]; for (let v = s; v < s + c; v++) col.set(k, v * 3); });
  geo.setAttribute('color', new Th.BufferAttribute(col, 3));
  const mat = new Th.MeshLambertMaterial({ vertexColors: true });
  const mesh = new Th.Mesh(geo, mat); mesh.frustumCulled = false; scene.add(mesh);
  scene.add(new Th.HemisphereLight(0xffffff, 0x667788, 1.6));
  const sun = new Th.DirectionalLight(0xffffff, 2); sun.position.set(1, 2, 3); scene.add(sun);
  const bs = geo.boundingSphere; const cam = new Th.PerspectiveCamera(45, 1280 / 800, 1, bs.radius * 10);
  const frame = t => {
    const a = t * 0.5, r = bs.radius * 1.6;
    cam.position.set(bs.center.x + Math.cos(a) * r, bs.center.y + Math.sin(a) * r, bs.center.z + r * 0.6);
    cam.up.set(0, 0, 1); cam.lookAt(bs.center); renderer.render(scene, cam);
  };
  frame(0); await gpuDone(); await nextFrame();
  const load_ms = performance.now() - t0;
  const out = { candidate: 'direct-three-' + backend, elements: meta.elements, triangles: meta.vertices / 3, load_ms: +load_ms.toFixed(0), fetch_ms: +(tFetch - t0).toFixed(0) };
  out.fps = await measureFps(frame, durMs);
  // GPU 바운드 프레임 시간: 렌더 직후 GPU 완료 대기 반복
  const g = []; for (let i = 0; i < 60; i++) { const s = performance.now(); frame(i / 10); await gpuDone(); g.push(performance.now() - s); }
  out.gpu_bound_frame_ms = stats(g.slice(5));
  // 요소 교체: 무작위 요소의 정점을 새 크기로 덮어쓰기 -> 다음 프레임까지 GPU 완료
  const rep = []; const core = []; const rnd = (() => { let s = 12345; return () => (s = (s * 1664525 + 1013904223) >>> 0) / 4294967296; })();
  const P = pa.array;
  for (let k = 0; k < 40; k++) {
    const [id, s, c] = meta.ranges[Math.floor(rnd() * meta.ranges.length)];
    const t1 = performance.now();
    let cx = 0, cy = 0; for (let v = s; v < s + c; v++) { cx += P[v * 3]; cy += P[v * 3 + 1]; } cx /= c; cy /= c;
    for (let v = s; v < s + c; v++) { P[v * 3] = cx + (P[v * 3] - cx) * 1.1; P[v * 3 + 1] = cy + (P[v * 3 + 1] - cy) * 1.1; }
    pa.clearUpdateRanges(); pa.addUpdateRange(s * 3, c * 3); pa.needsUpdate = true;
    frame(5 + k / 10); await gpuDone(); core.push(performance.now() - t1); await nextFrame();
    rep.push(performance.now() - t1);
  }
  out.replace_ms = stats(rep); out.replace_core_ms = stats(core); out.replace_note = '요소 정점범위 덮어쓰기+증분 업로드+프레임 GPU 완료+다음 rAF';
  Object.assign(out, mem());
  out.renderer_info = renderer.info ? { calls: renderer.info.render.calls, triangles: renderer.info.render.triangles } : null;
  return out;
};
