// headless 브라우저가 실제 GPU를 잡는지 확인 (WebGL2 렌더러 문자열·WebGPU 어댑터)
import { chromium } from 'playwright-core';
const args = process.argv.slice(2);
const b = await chromium.launch({ channel: 'msedge', headless: true,
  args: ['--enable-unsafe-webgpu','--enable-features=Vulkan,WebGPU','--use-angle=d3d11','--ignore-gpu-blocklist','--enable-gpu', ...args] });
const p = await b.newPage();
await p.goto('about:blank');
const r = await p.evaluate(async () => {
  const c = document.createElement('canvas'); const gl = c.getContext('webgl2');
  const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
  const out = { webgl2: !!gl, renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null, ua: navigator.userAgent };
  try { const a = await navigator.gpu?.requestAdapter(); out.webgpu = a ? (a.info ? {vendor:a.info.vendor, arch:a.info.architecture, desc:a.info.description} : 'adapter') : null; } catch (e) { out.webgpu = 'err ' + e; }
  return out;
});
console.log(JSON.stringify(r, null, 1)); await b.close();
