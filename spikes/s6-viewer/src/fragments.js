// 후보 B: That Open Fragments (@thatopen/fragments, MIT) + three.js WebGL.
// IFC는 Node에서 .frag로 변환해 둔 파일을 로드(변환 시간은 별도 기록). 교체 = editor.edit(UPDATE_SAMPLE)로 표현(geometry) 교체.
import * as THREE from 'three';
import * as FRAGS from '@thatopen/fragments';
import { measureFps, nextFrame, stats, mem } from './common.js';

window.run = async ({ base, durMs = 5000, replaceN = 40 }) => {
  const t0 = performance.now();
  const buf = await fetch(`${base}.frag`).then(r => r.arrayBuffer()); const fragBytes = buf.byteLength;
  const tFetch = performance.now();
  const canvas = document.getElementById('c');
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setSize(1280, 800, false);
  const gl = renderer.getContext(); const px = new Uint8Array(4); const gpuDone = async () => gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  const scene = new THREE.Scene(); scene.background = new THREE.Color(0xdde3ea);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x667788, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 2); sun.position.set(1, 2, 3); scene.add(sun);
  const cam = new THREE.PerspectiveCamera(45, 1280 / 800, 0.5, 2000);
  const fragments = new FRAGS.FragmentsModels('/dist/fragments-worker.mjs');
  const model = await fragments.load(buf, { modelId: 'm', camera: cam });
  model.useCamera(cam);
  scene.add(model.object);
  // 변환 좌표(Z-up IFC) -> 모델 바운딩박스로 카메라 거리 결정
  await fragments.update(true);
  const box = new THREE.Box3().setFromObject(model.object); if (box.isEmpty()) box.set(new THREE.Vector3(0, 0, 0), new THREE.Vector3(150, 96, 80));
  const c = box.getCenter(new THREE.Vector3()), r = box.getSize(new THREE.Vector3()).length() * 0.6;
  // 모델은 Y-up 변환되어 있을 수 있으므로 바운딩 박스 중심 기준으로 둘러본다
  const frame = t => {
    const a = t * 0.5;
    cam.position.set(c.x + Math.cos(a) * r, c.y + r * 0.6, c.z + Math.sin(a) * r); cam.lookAt(c);
    cam.updateMatrixWorld(); fragments.update(); renderer.render(scene, cam);
  };
  frame(0); await fragments.update(true); frame(0); await gpuDone(); await nextFrame();
  const load_ms = performance.now() - t0;
  const out = { candidate: 'fragments', fragments_version: '3.6.1', frag_bytes: fragBytes, load_ms: +load_ms.toFixed(0), fetch_ms: +(tFetch - t0).toFixed(0), bbox: box.getSize(new THREE.Vector3()).toArray().map(v => +v.toFixed(1)) };
  const ids = await model.getItemsIdsWithGeometry(); out.elements = ids.length;
  out.fps = await measureFps(frame, durMs);
  const g = []; for (let i = 0; i < 60; i++) { const s = performance.now(); frame(i / 10); await gpuDone(); g.push(performance.now() - s); }
  out.gpu_bound_frame_ms = stats(g.slice(5));
  out.draw = { calls: renderer.info.render.calls, triangles: renderer.info.render.triangles };
  // 요소 교체 3종: A=삭제+생성+applyChanges, B=삭제+생성(applyChanges 없음), C=기존 요소 변환(UPDATE_LOCAL_TRANSFORM)만 편집
  const rnd = (() => { let s = 777; return () => (s = (s * 1664525 + 1013904223) >>> 0) / 4294967296; })();
  const mat = new THREE.MeshLambertMaterial({ color: 0xcc6644 });
  const variants = {};
  const runVariant = async (name, fn) => {
    const rep = [], core = [], phEdit = [], phUpd = []; let err = null;
    for (let k = 0; k < replaceN; k++) {
      const t1 = performance.now();
      try { await fn(k); phEdit.push(performance.now() - t1); const tu = performance.now(); await fragments.update(true); phUpd.push(performance.now() - tu); frame(5 + k / 10); await gpuDone(); core.push(performance.now() - t1); await nextFrame(); }
      catch (e) { err = String(e && e.stack || e).slice(0, 500); break; }
      rep.push(performance.now() - t1);
    }
    variants[name] = { phase_edit_ms_median: phEdit.length ? stats(phEdit).median : null, phase_update_ms_median: phUpd.length ? stats(phUpd).median : null, replace_ms: rep.length ? stats(rep) : null, replace_core_ms: core.length ? stats(core) : null, error: err };
  };
  const mk = k => ({ attributes: { _category: { value: 'IFCWALL' }, _guid: { value: 'rep' + k + '-' + Date.now() + '-' + Math.random() } }, globalTransform: new THREE.Matrix4().makeTranslation(rnd() * 100, 0, rnd() * 100), samples: [{ localTransform: new THREE.Matrix4(), representation: new THREE.BoxGeometry(0.5, 3, 0.5), material: mat }] });
  await runVariant('A_delete_create_apply', async k => {
    const id = ids[Math.floor(rnd() * ids.length)]; const els = await fragments.editor.getElements('m', [id]);
    fragments.editor.deleteElements('m', els); await fragments.editor.createElements('m', [mk(k)]); await fragments.editor.applyChanges('m');
  });
  await runVariant('B_delete_create', async k => {
    const id = ids[Math.floor(rnd() * ids.length)]; const els = await fragments.editor.getElements('m', [id]);
    fragments.editor.deleteElements('m', els); await fragments.editor.createElements('m', [mk(k)]);
  });
  const ltIds = await model.getLocalTransformsIds(); const ltAll = await model.getLocalTransforms(ltIds);
  out.local_transforms = ltIds.length;
  await runVariant('C_update_local_transform', async () => {
    const lid = ltIds[Math.floor(rnd() * ltIds.length)]; const lt = ltAll.get(lid);
    await fragments.editor.edit('m', [{ type: FRAGS.EditRequestType.UPDATE_LOCAL_TRANSFORM, localId: lid, data: { ...lt, position: [lt.position[0] + 0.1, lt.position[1], lt.position[2]] } }]);
  });
  out.replace_variants = variants;
  const first = variants.A_delete_create_apply; out.replace_ms = first.replace_ms; out.replace_core_ms = first.replace_core_ms; if (first.error) out.replace_error = first.error;
  out.replace_note = 'editor.getElements/deleteElements/createElements/applyChanges + update(true) + 프레임 GPU 완료 + 다음 rAF';
  Object.assign(out, mem());
  return out;
};
