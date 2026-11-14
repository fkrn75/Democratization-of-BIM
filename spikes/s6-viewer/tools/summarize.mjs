// results/*.json 을 한 장의 마크다운 표로 요약한다. 사용: node tools/summarize.mjs
import fs from 'node:fs';
const rows = [];
for (const f of fs.readdirSync('results').filter(x => x.endsWith('.json') && !x.includes('geomonly') && x !== 'summary.json').sort()) {
  const r = JSON.parse(fs.readFileSync('results/' + f, 'utf8'));
  const n = v => (v === undefined || v === null ? '-' : (+v).toFixed(1));
  rows.push(`| ${f.replace('.json', '')} | ${r.elements ?? '-'} | ${r.load_ms ?? '-'} | ${n(r.fps?.fps)} | ${n(r.fps?.frame_ms?.p95)} | ${n(r.gpu_bound_frame_ms?.median)} | ${n(r.replace_ms?.median)} | ${n(r.replace_ms?.p95)} | ${r.jsHeapMB ?? '-'} | ${r.replace_error ? 'ERR' : ''} |`);
}
console.log('| 실행 | 요소 | 로드ms | fps(rAF) | rAF p95ms | GPU바운드 프레임ms(중앙) | 교체ms(중앙) | 교체p95 | JS힙MB | 비고 |\n|---|---|---|---|---|---|---|---|---|---|\n' + rows.join('\n'));
