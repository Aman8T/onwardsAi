/*
 * The cursor light's engine: pointer sampling, the wake (a chain of ten springs behind the pointer), the idle
 * state machine, the per-frame draw and the watchdog. Plain TypeScript with no React: CursorLight.tsx mounts
 * the canvas and hands it here.
 *
 * The loop runs only while something moves. Once the pointer has been still for 700 ms and the wake has
 * come to rest, the loop stops and the last frame stays on screen: zero frames while still. A timer wakes the
 * loop only for the slow dim (10 s) and the fade into sleep (45 s); after sleep the canvas is cleared and the
 * next pointer move starts the loop again. Per frame the main thread does a few smoothings, the spring
 * substeps, one uniform write for the chain and one scissored draw call; the pixels are the GPU's.
 */
import { createLightGL } from './gl';
import type { LightGL } from './gl';
import { LIGHT, wakeRadius, wanderOffset } from './palette';
import { FRAG, UNIFORMS, VERT } from './shader';
import type { Uniform } from './shader';

export type LightState = 'asleep' | 'awake' | 'leaving';

/** What the dev overlay shows (all measured, nothing estimated). */
export interface LightStats {
  state: LightState;
  alpha: number;
  frames: number;
  /** Frame-to-frame intervals over the last 300 frames, ms. */
  p50: number;
  p95: number;
  max: number;
  /** GPU time of the last measured frame and the worst seen, ms; null without the timer extension. */
  gpuMs: number | null;
  gpuMax: number | null;
  /** Main-thread time of one tick (smoothing, uniforms, the draw call), ms: median and worst of the last 300. */
  cpuMs: number;
  cpuMax: number;
  /** Whether the frame loop is running (false while the wake is still: the last frame stays on screen). */
  running: boolean;
  /** How far the tail of the wake sits behind the head right now, and the worst while moving. */
  lagPx: number;
  lagMs: number;
  maxLagPx: number;
  speed: number;
  stretch: number;
  scale: number;
  backing: [number, number];
  fieldHue: number;
  hue: number;
  /** The lowest OKLCH lightness drawn near the head in the last sample (dev only). */
  minL: number | null;
  heapMb: number | null;
}

export interface LightEngineOptions {
  /**
   * The fixed, pointer-transparent element the engine puts its own canvas into. The engine owns the canvas
   * (created on start, removed on destroy) so that every mount gets a fresh WebGL context: React may run an
   * effect twice on one element, and a context once lost on dispose cannot be reused.
   */
  host: HTMLElement;
  /** The field's hue and warm easing right now (the caller reads the root's inline style). */
  readField: () => { h: number; kw: number; dark?: boolean };
  /** Dev only: enables the timer query and the lightness sampler, and receives a snapshot a few times a second. */
  onStats?: (stats: LightStats) => void;
}

export interface LightEngine {
  destroy(): void;
  /** Dev harness: drive the pointer target along a path at about `pxPerSecond` for `seconds`. */
  sweep(seconds: number, pxPerSecond: number): void;
  /** Dev harness: override the render scale (the watchdog may lower it again). */
  setScale(scale: number): void;
  /** Dev harness: the measurements right now (also what the overlay shows). */
  snapshot(): LightStats;
}

interface State {
  /** The head: the latest pointer sample, CSS px. No smoothing. */
  tx: number;
  ty: number;
  /** The head at the previous frame, for velocity. */
  px: number;
  py: number;
  /** Followers' positions and velocities, CSS px. */
  fx: Float64Array;
  fy: Float64Array;
  fvx: Float64Array;
  fvy: Float64Array;
  dx: number;
  dy: number;
  speed: number;
  stretch: number;
  alpha: number;
  alphaTarget: number;
  state: LightState;
  lastMove: number;
  travel: number;
  seed: number;
  running: boolean;
  last: number;
  frames: number;
}

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));
const smooth = (dt: number, tau: number): number => 1 - Math.exp(-dt / tau);

/** OKLCH lightness of a gamma-encoded sRGB colour (0..1 channels). */
function oklabLightness(r: number, g: number, b: number): number {
  const lin = (c: number) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  const R = lin(r);
  const G = lin(g);
  const B = lin(b);
  const l = Math.cbrt(0.4122214708 * R + 0.5363325363 * G + 0.0514459929 * B);
  const m = Math.cbrt(0.2119034982 * R + 0.6806995451 * G + 0.1073969566 * B);
  const s = Math.cbrt(0.0883024619 * R + 0.2817188376 * G + 0.6299787005 * B);
  return 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
}

export function createLightEngine(opts: LightEngineOptions): LightEngine | null {
  const { host, readField, onStats } = opts;
  const dev = !!onStats;
  const canvas = document.createElement('canvas');
  canvas.className = 'light';
  canvas.setAttribute('aria-hidden', 'true');
  host.appendChild(canvas);
  const glx: LightGL<Uniform> | null = createLightGL(canvas, VERT, FRAG, UNIFORMS, dev);
  if (!glx) {
    canvas.remove();
    return null;
  }
  const { gl, uniforms: U, timer, dispose } = glx;
  gl.enable(gl.SCISSOR_TEST); // each frame clears and draws only the chain's bounding box

  const WK = LIGHT.wake;
  const N = WK.N;
  const st: State = {
    tx: 0, ty: 0, px: 0, py: 0,
    fx: new Float64Array(N), fy: new Float64Array(N), fvx: new Float64Array(N), fvy: new Float64Array(N),
    dx: 1, dy: 0, speed: 0, stretch: 0,
    alpha: 0, alphaTarget: 0, state: 'asleep', lastMove: -1e9, travel: 0,
    seed: Math.random() * 100, running: false, last: 0, frames: 0,
  };
  const nodes = new Float32Array((N + 1) * 2);
  const damping = 2 * WK.zeta * Math.sqrt(WK.K);

  /* ---- fitting the backing store ---- */
  let scale: number = LIGHT.render.scale;
  let W = 1;
  let H = 1;
  let texel = 1; // backing px per CSS px
  let radius: number = WK.radius.min;
  /** The box drawn last frame, backing px (x0, y0, x1, y1), so the next frame clears it too. */
  let prevBox: [number, number, number, number] | null = null;

  function fit(): void {
    W = Math.max(1, window.innerWidth);
    H = Math.max(1, window.innerHeight);
    texel = Math.min(scale, LIGHT.render.maxWidth / W, LIGHT.render.maxHeight / H);
    const bw = Math.max(1, Math.round(W * texel));
    const bh = Math.max(1, Math.round(H * texel));
    if (canvas.width !== bw || canvas.height !== bh) {
      canvas.width = bw;
      canvas.height = bh;
      gl.viewport(0, 0, bw, bh);
      prevBox = null; // resizing cleared the buffer
    }
    radius = wakeRadius(W, H);
  }
  fit();

  /* ---- field hue, read once a second (an inline-style read, no style recalculation) ---- */
  let field = readField();
  let fieldReadAt = -1e9;

  /* ---- the loop ---- */
  let raf = 0;
  let destroyed = false;
  let stillTimer = 0;

  function schedule(): void {
    raf = requestAnimationFrame(tick);
  }

  function clearStill(): void {
    if (stillTimer) {
      clearTimeout(stillTimer);
      stillTimer = 0;
    }
  }

  /** Starts the frame loop if it is stopped (waking, leaving or a timer while the last frame is on screen). */
  function resume(now: number): void {
    if (destroyed || st.running) return;
    clearStill();
    st.running = true;
    st.last = now;
    prevFrameAt = 0;
    schedule();
  }

  function wake(now: number): void {
    if (destroyed) return;
    if (st.state === 'asleep') {
      // wake where the pointer is: nothing travels across the screen to reach it
      st.px = st.tx;
      st.py = st.ty;
      st.fx.fill(st.tx);
      st.fy.fill(st.ty);
      st.fvx.fill(0);
      st.fvy.fill(0);
      st.speed = 0;
      st.stretch = 0;
    }
    st.state = 'awake';
    st.alphaTarget = 1;
    resume(now);
  }

  function sleepNow(): void {
    clearStill();
    st.state = 'asleep';
    st.alpha = 0;
    st.alphaTarget = 0;
    if (st.running) {
      st.running = false;
      cancelAnimationFrame(raf);
    }
    gl.scissor(0, 0, canvas.width, canvas.height);
    gl.clear(gl.COLOR_BUFFER_BIT);
    prevBox = null;
    if (onStats) onStats(snapshot());
  }

  function leave(): void {
    if (st.state === 'awake') {
      st.state = 'leaving';
      st.alphaTarget = 0;
      resume(performance.now()); // the fade needs frames, even if the wake was still
    }
  }

  /** The wake is still: stop drawing, keep the last frame, and wake only for the next idle step (dim, then sleep). */
  function settle(now: number): void {
    st.running = false;
    prevFrameAt = 0;
    const idle = (now - st.lastMove) / 1000;
    const next = idle < LIGHT.idle.dimAfter ? LIGHT.idle.dimAfter : LIGHT.idle.sleepAfter;
    const delay = Math.max(0, st.lastMove + next * 1000 - now) + 30;
    clearStill();
    stillTimer = window.setTimeout(() => {
      stillTimer = 0;
      resume(performance.now());
    }, delay);
    if (onStats) onStats(snapshot());
  }

  /* ---- dev measurements ---- */
  const intervals: number[] = [];
  const cpuTimes: number[] = [];
  let prevFrameAt = 0;
  let gpuMs: number | null = null;
  let gpuMax: number | null = null;
  let query: WebGLQuery | null = null;
  let queryOpen = false;
  let maxLagPx = 0;
  let lagMs = 0;
  let minL: number | null = null;
  let sampledAt = -1e9;
  const patch = dev ? new Uint8Array(64 * 64 * 4) : null;

  /* ---- the watchdog: long frames while the page is clearly struggling lower the scale, then stop ---- */
  let period = 0; // estimated display period, ms (median of the first frames)
  const periodSamples: number[] = [];
  let windowStart = 0;
  let windowFrames = 0;
  let windowLong = 0;
  let strikes = 0;

  function watch(now: number, dtMs: number): void {
    if (period === 0) {
      periodSamples.push(dtMs);
      if (periodSamples.length >= 30) {
        periodSamples.sort((a, b) => a - b);
        period = periodSamples[Math.floor(periodSamples.length / 2)];
        windowStart = now;
      }
      return;
    }
    // a gap of ten periods or more is the page being paused or throttled, not a slow frame: start over
    if (dtMs > period * 10) {
      windowStart = now;
      windowFrames = 0;
      windowLong = 0;
      return;
    }
    windowFrames++;
    if (dtMs > period * LIGHT.watchdog.long) windowLong++;
    if (now - windowStart < LIGHT.watchdog.window * 1000) return;
    if (windowFrames >= 20 && windowLong / windowFrames >= LIGHT.watchdog.share) {
      const next = LIGHT.watchdog.scales[strikes];
      strikes++;
      if (next === undefined) {
        if (import.meta.env.DEV) console.warn('cursor light: frames kept running long; off for this session');
        destroy();
        return;
      }
      scale = next;
      fit();
    }
    windowStart = now;
    windowFrames = 0;
    windowLong = 0;
  }

  /* ---- dev harness: synthetic motion ---- */
  let sweepUntil = 0;
  let sweepSpeed = 0;

  function tick(now: number): void {
    if (!st.running || destroyed) return;
    const tickStart = dev ? performance.now() : 0;
    // dt clamped to 4–50 ms: a repeated or earlier timestamp must never divide to NaN or Infinity
    const dtMs = now - st.last;
    const dt = clamp(dtMs / 1000, 0.004, 0.05);
    st.last = now;
    st.frames++;
    if (!Number.isFinite(st.speed) || !Number.isFinite(st.dx) || !Number.isFinite(st.fx[0]) || !Number.isFinite(st.fy[0])) {
      st.speed = 0;
      st.fx.fill(st.tx);
      st.fy.fill(st.ty);
      st.fvx.fill(0);
      st.fvy.fill(0);
      st.dx = 1;
      st.dy = 0;
      st.stretch = 0;
    }
    if (dev) {
      if (prevFrameAt) {
        intervals.push(now - prevFrameAt);
        if (intervals.length > 300) intervals.shift();
      }
      prevFrameAt = now;
    }
    watch(now, dtMs);
    if (destroyed) return;

    const T = now / 1000;

    if (now < sweepUntil) {
      // an ellipse through the middle of the window at about the requested speed
      const rx = W * 0.35;
      const ry = H * 0.3;
      const omega = sweepSpeed / ((rx + ry) / 2);
      st.tx = W / 2 + rx * Math.sin(T * omega);
      st.ty = H / 2 + ry * Math.sin(T * omega * 0.77 + 1);
      st.lastMove = now;
    }

    // the head is the pointer; its velocity is smoothed, its direction only updated while it is really moving
    const vx = (st.tx - st.px) / dt;
    const vy = (st.ty - st.py) / dt;
    const instSpeed = Math.hypot(vx, vy);
    st.speed += (instSpeed - st.speed) * smooth(dt, LIGHT.tau.speed);
    if (instSpeed > LIGHT.directionMinSpeed) {
      const kd = smooth(dt, LIGHT.tau.direction);
      st.dx += (vx / instSpeed - st.dx) * kd;
      st.dy += (vy / instSpeed - st.dy) * kd;
    }
    const dl = Math.hypot(st.dx, st.dy) || 1;
    const dirX = st.dx / dl;
    const dirY = st.dy / dl;
    st.stretch = Math.min(st.speed / WK.stretch.fullSpeed, 1) * WK.stretch.max;
    st.travel += Math.hypot(st.tx - st.px, st.ty - st.py);

    // the wake: follower 0 springs toward the head, follower i toward follower i-1; fixed substeps of at most
    // 4 ms, the head moving in a straight line across the frame, so 60 Hz and 120 Hz screens behave the same
    const steps = Math.ceil(dt / WK.substep);
    const h = dt / steps;
    for (let s = 1; s <= steps; s++) {
      const f = s / steps;
      let targetX = st.px + (st.tx - st.px) * f;
      let targetY = st.py + (st.ty - st.py) * f;
      for (let i = 0; i < N; i++) {
        st.fvx[i] += (WK.K * (targetX - st.fx[i]) - damping * st.fvx[i]) * h;
        st.fvy[i] += (WK.K * (targetY - st.fy[i]) - damping * st.fvy[i]) * h;
        st.fx[i] += st.fvx[i] * h;
        st.fy[i] += st.fvy[i] * h;
        targetX = st.fx[i];
        targetY = st.fy[i];
      }
    }
    st.px = st.tx;
    st.py = st.ty;

    // how much the wake still moves: the settle test
    let energy = 0;
    for (let i = 0; i < N; i++) energy += Math.hypot(st.fvx[i], st.fvy[i]) + Math.hypot(st.fx[i] - st.tx, st.fy[i] - st.ty);

    // the idle schedule: full, then dim, then gone
    const idle = (now - st.lastMove) / 1000;
    if (st.state === 'awake') {
      if (idle > LIGHT.idle.sleepAfter) {
        st.state = 'leaving';
        st.alphaTarget = 0;
      } else if (idle > LIGHT.idle.dimAfter) {
        st.alphaTarget = LIGHT.idle.dimTo;
      }
    }
    const rising = st.alphaTarget > st.alpha;
    const tauAlpha = rising ? LIGHT.tau.wake : st.state === 'leaving' ? (idle > LIGHT.idle.sleepAfter ? LIGHT.tau.sleep : LIGHT.tau.leave) : LIGHT.tau.dim;
    st.alpha += (st.alphaTarget - st.alpha) * smooth(dt, tauAlpha);
    if (st.state === 'leaving' && st.alpha < 0.004) {
      sleepNow();
      return;
    }

    // colour: the dye of the scheme, its head hue the field's plus 24 degrees and a small wander
    if (now - fieldReadAt > 1000) {
      field = readField();
      fieldReadAt = now;
    }
    const hue = field.h + WK.hue.head + wanderOffset(T, st.travel);
    const dye = field.dark ? WK.tone.dark : WK.tone.light;
    const peak = field.dark ? WK.alpha.dark : WK.alpha.light;

    // the chain in backing px, y up, and its bounding box
    nodes[0] = st.tx * texel;
    nodes[1] = (H - st.ty) * texel;
    let x0 = nodes[0];
    let x1 = nodes[0];
    let y0 = nodes[1];
    let y1 = nodes[1];
    let maxSeg = 0;
    for (let i = 0; i < N; i++) {
      const nx = st.fx[i] * texel;
      const ny = (H - st.fy[i]) * texel;
      nodes[2 * (i + 1)] = nx;
      nodes[2 * (i + 1) + 1] = ny;
      maxSeg = Math.max(maxSeg, Math.hypot(nx - nodes[2 * i], ny - nodes[2 * i + 1]));
      if (nx < x0) x0 = nx;
      if (nx > x1) x1 = nx;
      if (ny < y0) y0 = ny;
      if (ny > y1) y1 = ny;
    }
    const pad = 2.2 * radius * texel * (1 + st.stretch) + (2.2 * WK.stretch.segment + 0.5) * maxSeg + 2;
    const bx0 = clamp(Math.floor(x0 - pad), 0, canvas.width);
    const by0 = clamp(Math.floor(y0 - pad), 0, canvas.height);
    const bx1 = clamp(Math.ceil(x1 + pad), 0, canvas.width);
    const by1 = clamp(Math.ceil(y1 + pad), 0, canvas.height);

    if (dev) {
      const lag = Math.hypot(st.tx - st.fx[N - 1], st.ty - st.fy[N - 1]);
      if (st.speed > 200) {
        maxLagPx = Math.max(maxLagPx, lag);
        lagMs = (lag / st.speed) * 1000;
      }
      if (timer && !query) {
        query = gl.createQuery();
        if (query) {
          gl.beginQuery(timer.TIME_ELAPSED_EXT, query);
          queryOpen = true;
        }
      }
    }

    // clear last frame's box and this one's, then draw into this one's only
    if (prevBox) {
      const cx0 = Math.min(prevBox[0], bx0);
      const cy0 = Math.min(prevBox[1], by0);
      gl.scissor(cx0, cy0, Math.max(prevBox[2], bx1) - cx0, Math.max(prevBox[3], by1) - cy0);
    } else {
      gl.scissor(bx0, by0, bx1 - bx0, by1 - by0);
    }
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.scissor(bx0, by0, bx1 - bx0, by1 - by0);
    prevBox = [bx0, by0, bx1, by1];

    gl.uniform2fv(U.uNodes, nodes);
    gl.uniform2f(U.uDir, dirX, -dirY);
    gl.uniform1f(U.uStretch, st.stretch);
    gl.uniform1f(U.uRadius, radius * texel);
    gl.uniform1f(U.uTime, T % 10000);
    gl.uniform1f(U.uSeed, st.seed);
    gl.uniform1f(U.uAlpha, peak * st.alpha);
    gl.uniform1f(U.uHue, hue);
    gl.uniform4f(U.uTone, dye.head.L, dye.head.C, dye.tail.L, dye.tail.C);
    gl.drawArrays(gl.TRIANGLES, 0, 3);

    if (dev) {
      if (timer && queryOpen) {
        gl.endQuery(timer.TIME_ELAPSED_EXT);
        queryOpen = false;
      } else if (timer && query) {
        const ready = gl.getQueryParameter(query, gl.QUERY_RESULT_AVAILABLE) as boolean;
        const disjoint = gl.getParameter(timer.GPU_DISJOINT_EXT) as boolean;
        if (ready && !disjoint) {
          gpuMs = (gl.getQueryParameter(query, gl.QUERY_RESULT) as number) / 1e6;
          gpuMax = Math.max(gpuMax ?? 0, gpuMs);
        }
        if (ready || disjoint) {
          gl.deleteQuery(query);
          query = null;
        }
      }
      // the tick's own cost, measured before the once-a-second sampler (which stalls the pipeline on purpose)
      cpuTimes.push(performance.now() - tickStart);
      if (cpuTimes.length > 300) cpuTimes.shift();
      if (patch && now - sampledAt > 1000 && st.alpha > 0.5) {
        sampledAt = now;
        sampleLightness();
      }
      if (st.frames % 15 === 0) report();
    }

    // still: no frames until the pointer moves again or the idle schedule needs one
    if (
      st.state === 'awake' &&
      now - st.lastMove > LIGHT.settle.after &&
      st.speed < LIGHT.settle.speed &&
      energy < LIGHT.settle.energy * N &&
      Math.abs(st.alpha - st.alphaTarget) < 0.004
    ) {
      settle(now);
      return;
    }

    schedule();
  }

  /** Reads a 64x64 patch around the head and records the lowest lightness of any pixel the light actually colours. */
  function sampleLightness(): void {
    if (!patch) return;
    const cx = Math.round(st.tx * texel);
    const cy = Math.round((H - st.ty) * texel);
    const x0 = clamp(cx - 32, 0, Math.max(0, canvas.width - 64));
    const y0 = clamp(cy - 32, 0, Math.max(0, canvas.height - 64));
    gl.readPixels(x0, y0, 64, 64, gl.RGBA, gl.UNSIGNED_BYTE, patch);
    let lowest = 1;
    for (let i = 0; i < patch.length; i += 4) {
      const a = patch[i + 3] / 255;
      if (a < 0.1) continue;
      const L = oklabLightness(patch[i] / 255 / a, patch[i + 1] / 255 / a, patch[i + 2] / 255 / a);
      if (L < lowest) lowest = L;
    }
    minL = minL === null ? lowest : Math.min(minL, lowest);
  }

  function report(): void {
    if (onStats) onStats(snapshot());
  }

  function snapshot(): LightStats {
    const sorted = [...intervals].sort((a, b) => a - b);
    const q = (p: number) => (sorted.length ? sorted[Math.floor((sorted.length - 1) * p)] : 0);
    const cpuSorted = [...cpuTimes].sort((a, b) => a - b);
    const memory = (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory;
    return {
      state: st.state,
      alpha: st.alpha,
      frames: st.frames,
      running: st.running,
      p50: q(0.5),
      p95: q(0.95),
      max: q(1),
      gpuMs,
      gpuMax,
      cpuMs: cpuSorted.length ? cpuSorted[Math.floor((cpuSorted.length - 1) * 0.5)] : 0,
      cpuMax: cpuSorted.length ? cpuSorted[cpuSorted.length - 1] : 0,
      lagPx: Math.hypot(st.tx - st.fx[N - 1], st.ty - st.fy[N - 1]),
      lagMs,
      maxLagPx,
      speed: st.speed,
      stretch: st.stretch,
      scale,
      backing: [canvas.width, canvas.height],
      fieldHue: field.h,
      hue: (((field.h + WK.hue.head + wanderOffset(performance.now() / 1000, st.travel)) % 360) + 360) % 360,
      minL,
      heapMb: memory ? memory.usedJSHeapSize / 1048576 : null,
    };
  }

  /* ---- events ---- */
  const onMove = (e: PointerEvent): void => {
    if (e.pointerType === 'touch') return;
    // the latest sample, not the frame's first: the head is exactly where the pointer is
    const co = typeof e.getCoalescedEvents === 'function' ? e.getCoalescedEvents() : [];
    const sample = co.length ? co[co.length - 1] : e;
    st.tx = sample.clientX;
    st.ty = sample.clientY;
    const now = performance.now();
    st.lastMove = now;
    wake(now);
  };
  const onOut = (e: PointerEvent): void => {
    if (e.relatedTarget === null) leave(); // left the window, not just an element
  };
  const onBlur = (): void => leave();
  const onVisibility = (): void => {
    if (document.visibilityState === 'hidden') sleepNow();
  };
  const onResize = (): void => {
    fit();
    // a still wake lost its frame to the resize: draw it once more
    if (st.state === 'awake' && !st.running) resume(performance.now());
  };
  const onContextLost = (e: Event): void => {
    e.preventDefault();
    destroy();
  };

  window.addEventListener('pointermove', onMove, { passive: true });
  window.addEventListener('pointerout', onOut, { passive: true });
  window.addEventListener('blur', onBlur);
  window.addEventListener('resize', onResize);
  document.addEventListener('visibilitychange', onVisibility);
  canvas.addEventListener('webglcontextlost', onContextLost);

  function destroy(): void {
    if (destroyed) return;
    destroyed = true;
    st.running = false;
    cancelAnimationFrame(raf);
    clearStill();
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerout', onOut);
    window.removeEventListener('blur', onBlur);
    window.removeEventListener('resize', onResize);
    document.removeEventListener('visibilitychange', onVisibility);
    canvas.removeEventListener('webglcontextlost', onContextLost);
    if (query) gl.deleteQuery(query);
    dispose();
    canvas.remove();
  }

  return {
    destroy,
    sweep(seconds, pxPerSecond) {
      const now = performance.now();
      sweepUntil = now + seconds * 1000;
      sweepSpeed = pxPerSecond;
      maxLagPx = 0;
      st.lastMove = now;
      wake(now);
    },
    setScale(next) {
      scale = clamp(next, 0.1, 1);
      fit();
    },
    snapshot,
  };
}
