// BimCraft 브라우저판 탐침 (컨테이너 안 헤드리스 Chromium): 로드, 콘솔 오류, 파일 입력, RA-01 가져오기 시도, 스크린샷.
import { createRequire } from 'node:module';
import fs from 'node:fs';
const puppeteer = createRequire('/opt/pp/package.json')('puppeteer-core');
const rec = { console: [], steps: [] };
const t0 = Date.now();
const browser = await puppeteer.launch({ executablePath: '/usr/bin/chromium', headless: true, args: ['--no-sandbox', '--disable-gpu', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--window-size=1440,900'] });
const page = await browser.newPage();
page.on('console', m => { if (['error', 'warning'].includes(m.type())) rec.console.push(m.type() + ': ' + m.text().slice(0, 200)); });
page.on('pageerror', e => rec.console.push('pageerror: ' + String(e).slice(0, 200)));
page.on('requestfailed', r => rec.console.push('requestfailed: ' + r.url().slice(0, 120)));
await page.goto('http://127.0.0.1:8000/', { waitUntil: 'networkidle2', timeout: 90000 }).catch(e => rec.steps.push('goto: ' + e.message));
rec.load_ms = Date.now() - t0;
rec.title = await page.title();
await new Promise(r => setTimeout(r, 4000));
rec.body_text = (await page.evaluate(() => document.body.innerText)).slice(0, 1200);
rec.file_inputs = await page.evaluate(() => [...document.querySelectorAll('input[type=file]')].map(i => ({ accept: i.accept, id: i.id, multiple: i.multiple })));
await page.screenshot({ path: '/out/bimcraft_start.png' });
const idx = rec.file_inputs.findIndex(i => !i.accept || /ifc|\*/i.test(i.accept));
rec.chosen_input = idx;
if (idx >= 0) {
  const inputs = await page.$$('input[type=file]');
  fs.mkdirSync('/out/dl', { recursive: true });
  const cdp = await browser.target().createCDPSession();
  await cdp.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: '/out/dl' });
  await inputs[idx].uploadFile('/in/RA-01.ifc');
  await new Promise(r => setTimeout(r, 25000));
  rec.after_upload_text = (await page.evaluate(() => document.body.innerText)).slice(0, 2500);
  await page.screenshot({ path: '/out/bimcraft_after_import.png' });
}
const clicked = await page.evaluate(() => { const b = [...document.querySelectorAll('button,a')].find(x => /^\s*Save as IFC\s*$/i.test(x.innerText || '')); if (b) { b.click(); return true; } return false; });
rec.save_clicked = clicked;
await new Promise(r => setTimeout(r, 6000));
rec.downloads = fs.existsSync('/out/dl') ? fs.readdirSync('/out/dl') : [];
rec.globals = await page.evaluate(() => Object.keys(window).filter(k => /bim|cad|ifc|addon|api/i.test(k)).slice(0, 30));
fs.writeFileSync('/out/bimcraft_probe.json', JSON.stringify(rec, null, 1));
await browser.close();
console.log('DONE', JSON.stringify({ title: rec.title, load_ms: rec.load_ms, inputs: rec.file_inputs.length, errs: rec.console.length, globals: rec.globals }));
