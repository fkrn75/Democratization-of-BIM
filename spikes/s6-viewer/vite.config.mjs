// ifc-lite는 WASM/워커를 import.meta.url로 찾으므로 vite로 번들한다. 결과는 dist/ 로 모은다.
import { defineConfig } from 'vite';
export default defineConfig({
  root: 'vite-root',
  base: './',
  build: { outDir: '../dist', emptyOutDir: false, target: 'es2022', rollupOptions: { input: 'vite-root/iflite.html' } },
  worker: { format: 'es' },
  optimizeDeps: { exclude: ['@ifc-lite/wasm', '@ifc-lite/geometry'] },
});
