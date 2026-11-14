import { chromium } from 'playwright-core'; import { serve } from './serve.mjs';
const { server, port } = await serve(process.cwd());
const b = await chromium.launch({ channel: 'msedge', headless: true, args: ['--enable-unsafe-webgpu','--enable-features=Vulkan,WebGPU','--ignore-gpu-blocklist', ...process.argv.slice(2)] });
const p = await b.newPage(); await p.goto(`http://127.0.0.1:${port}/package.json`);
console.log(JSON.stringify(await p.evaluate(async()=>{ if(!navigator.gpu) return 'no navigator.gpu'; const a=await navigator.gpu.requestAdapter(); return a?{vendor:a.info.vendor,arch:a.info.architecture,desc:a.info.description,fallback:a.isFallbackAdapter}:'null adapter';})));
await b.close(); server.close();
