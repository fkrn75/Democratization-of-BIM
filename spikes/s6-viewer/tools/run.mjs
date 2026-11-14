// 사용: node tools/run.mjs <후보> <모델기본명> [옵션JSON] -> results/<후보>[-backend]-<모델>.json
// headless msedge(실 GPU)로 dist/<후보>.html 실행, window.run(cfg) 결과 저장. 창 없음.
import { chromium } from 'playwright-core'; import fs from 'node:fs'; import { serve } from './serve.mjs';
const [name, model, optJson] = process.argv.slice(2); const opt = optJson ? JSON.parse(optJson) : {};
const { server, port } = await serve(process.cwd());
const b = await chromium.launch({ channel: 'msedge', headless: true, args: ['--enable-unsafe-webgpu', '--enable-features=Vulkan,WebGPU', '--ignore-gpu-blocklist', '--enable-precise-memory-info', ...(opt.args || [])] });
const p = await b.newPage({ viewport: { width: 1280, height: 800 } });
p.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') console.error('[page]', m.text().slice(0, 300)); });
p.on('pageerror', e => console.error('[pageerror]', String(e).slice(0, 500)));
await p.goto(`http://127.0.0.1:${port}/dist/${name}.html`);
const gpuInfo = await p.evaluate(async () => {
  const gl = document.createElement('canvas').getContext('webgl2'); const e = gl.getExtension('WEBGL_debug_renderer_info'); let a = null;
  try { const ad = await navigator.gpu?.requestAdapter(); a = ad ? ad.info.vendor + '/' + ad.info.architecture + (ad.isFallbackAdapter ? ' FALLBACK' : '') : null; } catch {}
  return { webgl: gl.getParameter(e.UNMASKED_RENDERER_WEBGL), webgpu: a };
});
const res = await p.evaluate(o => (o.geomOnly ? window.runGeomOnly : window.run)(o), { base: `/model/${model}`, ...opt });
res.env = { gpu: gpuInfo, browser: b.version(), viewport: '1280x800' };
fs.mkdirSync('results', { recursive: true });
const out = `results/${name}${opt.backend ? '-' + opt.backend : ''}${opt.geomOnly ? '-geomonly' : ''}${opt.instancing ? '-inst' : ''}-${model}.json`; fs.writeFileSync(out, JSON.stringify(res, null, 1)); console.log(JSON.stringify(res, null, 1));
await b.close(); server.close();
