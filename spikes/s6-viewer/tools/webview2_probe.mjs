// WebView2 동작 근거: WebView2 Evergreen 런타임과 같은 Chromium 빌드를 플래그 없이(기본값) 띄워 WebGPU/WebGL2 가용성 확인.
// (실제 WebView2 호스트 앱 안에서의 시험은 아님 — 창 없이 가능한 범위.) 사용: node tools/webview2_probe.mjs
import { chromium } from 'playwright-core'; import { serve } from './serve.mjs'; import fs from 'node:fs';
const { server, port } = await serve(process.cwd());
const b = await chromium.launch({ channel: 'msedge', headless: true, args: [] }); // 특수 플래그 없음
const p = await b.newPage(); await p.goto(`http://127.0.0.1:${port}/package.json`);
const r = await p.evaluate(async () => {
  const gl = document.createElement('canvas').getContext('webgl2'); const e = gl && gl.getExtension('WEBGL_debug_renderer_info');
  const a = navigator.gpu ? await navigator.gpu.requestAdapter() : null;
  return { webgl2: !!gl, webgl_renderer: e ? gl.getParameter(e.UNMASKED_RENDERER_WEBGL) : null, navigator_gpu: !!navigator.gpu, webgpu_adapter: a ? { vendor: a.info.vendor, arch: a.info.architecture } : null, sab: typeof SharedArrayBuffer !== 'undefined', crossOriginIsolated };
});
r.browser = b.version();
try { const base = 'C:/Program Files (x86)/Microsoft/EdgeWebView/Application'; r.webview2_runtime_versions = fs.readdirSync(base).filter(x => /^\d+\./.test(x)); } catch { r.webview2_runtime_versions = null; }
fs.writeFileSync('results/webview2-probe.json', JSON.stringify(r, null, 1)); console.log(JSON.stringify(r, null, 1));
await b.close(); server.close();
