// IFC -> Fragments(.frag) 변환(Node, CPU만). 변환 시간 기록. 사용: node tools/ifc2frag.mjs <in.ifc> <out.frag>
import * as FRAGS from '@thatopen/fragments'; import fs from 'node:fs'; import path from 'node:path';
const [inp, out] = process.argv.slice(2);
const imp = new FRAGS.IfcImporter();
imp.wasm = { path: path.resolve('node_modules/web-ifc') + path.sep, absolute: true };
const bytes = new Uint8Array(fs.readFileSync(inp));
const t0 = performance.now();
const frag = await imp.process({ bytes, progressCallback: () => {} });
const ms = performance.now() - t0;
fs.writeFileSync(out, frag);
console.log(JSON.stringify({ in: inp, frag_bytes: frag.byteLength, convert_ms: Math.round(ms) }));
