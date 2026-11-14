// 공통 측정 유틸: rAF 기반 fps(카메라 궤도 회전), 교체 지연 통계
export const nextFrame = () => new Promise(r => requestAnimationFrame(r));
export const stats = a => {
  const s = [...a].sort((x, y) => x - y), q = p => s[Math.min(s.length - 1, Math.floor(p * s.length))];
  return { n: s.length, min: s[0], median: q(0.5), p95: q(0.95), max: s[s.length - 1], mean: s.reduce((x, y) => x + y, 0) / s.length };
};
// frameFn(t): 카메라를 t(초)에 맞춰 돌리고 렌더 호출. durMs 동안 rAF 루프 실행.
export async function measureFps(frameFn, durMs = 5000) {
  const dts = []; let last = performance.now(); const t0 = last; let frames = 0;
  await new Promise(res => {
    const loop = now => {
      frameFn((now - t0) / 1000); frames++; dts.push(now - last); last = now;
      if (now - t0 < durMs) requestAnimationFrame(loop); else res();
    };
    requestAnimationFrame(loop);
  });
  const st = stats(dts.slice(2));
  return { frames, seconds: (last - t0) / 1000, fps: (frames - 2) / ((last - t0 - dts[0] - dts[1]) / 1000), frame_ms: st };
}
export const mem = () => performance.memory ? { jsHeapMB: +(performance.memory.usedJSHeapSize / 1048576).toFixed(1) } : {};
