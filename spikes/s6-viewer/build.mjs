// esbuild 번들: src/<name>.js -> dist/<name>.js (+ html)
import { build } from 'esbuild'; import fs from 'node:fs';
const names = process.argv.slice(2); fs.mkdirSync('dist', { recursive: true });
for (const n of names) {
  await build({ entryPoints: [`src/${n}.js`], bundle: true, format: 'esm', outfile: `dist/${n}.js`, platform: 'browser', target: 'es2022', loader: { '.wasm': 'file' }, logLevel: 'warning' });
  fs.writeFileSync(`dist/${n}.html`, `<!doctype html><meta charset=utf-8><canvas id=c width=1280 height=800></canvas><script type=module src="./${n}.js"></script>`);
}
