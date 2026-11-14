// 정적 파일 서버(localhost, 보안 컨텍스트 확보 + COOP/COEP 헤더). 사용: serve(root, port)
import http from 'node:http'; import fs from 'node:fs'; import path from 'node:path';
const T = { '.html':'text/html', '.js':'text/javascript', '.mjs':'text/javascript', '.wasm':'application/wasm', '.ifc':'application/octet-stream', '.json':'application/json', '.frag':'application/octet-stream' };
export function serve(root, port = 0) {
  const s = http.createServer((q, r) => {
    const p = path.join(root, decodeURIComponent(q.url.split('?')[0]));
    if (!p.startsWith(root) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { r.writeHead(404); return r.end(); }
    r.writeHead(200, { 'content-type': T[path.extname(p)] || 'application/octet-stream', 'cross-origin-opener-policy': 'same-origin', 'cross-origin-embedder-policy': 'require-corp' });
    fs.createReadStream(p).pipe(r);
  });
  return new Promise(res => s.listen(port, '127.0.0.1', () => res({ server: s, port: s.address().port })));
}
