/*
 * The engine: the lazily loaded chunk that owns the scene, the creature, the fire, the scorches, the mind
 * and the frame loop. `startDragon` is the only entry point; DragonLayer calls it after Home has painted
 * and destroys the handle when Home goes. The loop runs only while the creature is awake and the tab is
 * visible; dozing or hidden, the page produces no frames.
 */
import { Vector3 } from 'three';
import { BurnLayer } from './burn';
import { Dragon } from './dragon';
import type { Pose } from './dragon';
import { Egg } from './egg';
import { Fire } from './fire';
import { Growth } from './growth';
import type { Stage } from './growth';
import { Mind } from './mind';
import type { Mood, Pointer } from './mind';
import { DragonScene } from './scene';
import { DEFAULT_SPECIES, speciesById } from './species';
import type { Species } from './species';
import { dragonState, takeDragonCommands } from './state';

export interface DragonStats {
  species: string;
  stage: string;
  age: number;
  scale: number;
  mood: Mood;
  hunger: number;
  fatigue: number;
  burns: number;
  frames: number;
  p50: number;
  p95: number;
  max: number;
  gpuMs: number | null;
  gpuMax: number | null;
  cpuMs: number;
  cpuMax: number;
  drawCalls: number;
  triangles: number;
  particles: number;
  dpr: number;
  clips: string;
  heapMb: number | null;
}

export interface StartOptions {
  host: HTMLElement;
  /** Dev: begin at this age in seconds (0 = a new egg). */
  startAge?: number;
  /** Dev: breathe as soon as it can. */
  huntNow?: boolean;
  /** Dev: run the loop from a 16 ms timer instead of requestAnimationFrame. */
  timerLoop?: boolean;
  onStats?: (s: DragonStats) => void;
}

export interface DragonHandle {
  /** Home is leaving: stop deciding; the host fades through CSS. */
  retire(): void;
  destroy(): void;
}

interface TimerExt {
  TIME_ELAPSED_EXT: number;
  GPU_DISJOINT_EXT: number;
}

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));

export async function startDragon(opts: StartOptions): Promise<DragonHandle | null> {
  const { host, onStats } = opts;
  const dev = !!onStats;
  let destroyed = false;

  const scene = new DragonScene(host);
  const burns = new BurnLayer();
  const fire = new Fire(scene.scene);
  const growth = new Growth({ defaultSpecies: DEFAULT_SPECIES, startAge: opts.startAge, onStage: () => celebrate() });
  const mind = new Mind();
  const egg = new Egg(48);
  scene.scene.add(egg.mesh);
  egg.mesh.visible = false;

  let species: Species = speciesById(growth.species);
  let dragon: Dragon | null = null;
  const loadDragon = async (sp: Species): Promise<boolean> => {
    try {
      const next = await Dragon.load(sp);
      if (destroyed) {
        next.dispose();
        return false;
      }
      dragon?.dispose();
      dragon = next;
      species = sp;
      scene.scene.add(next.root);
      dragonState.report({ species: sp.id, ready: true });
      return true;
    } catch (err) {
      if (import.meta.env.DEV) console.error('dragon: could not load', sp.model, err);
      return false;
    }
  };
  if (!(await loadDragon(species))) {
    if (species.id !== DEFAULT_SPECIES && (await loadDragon(speciesById(DEFAULT_SPECIES)))) growth.setSpecies(DEFAULT_SPECIES);
    else {
      burns.dispose();
      fire.dispose();
      egg.dispose();
      scene.dispose();
      return null;
    }
  }
  if (opts.huntNow) mind.huntNow();

  /* ---- pointer, visibility, size ---- */
  let pointer: Pointer | null = null;
  const onMove = (e: PointerEvent): void => {
    if (e.pointerType === 'touch') return;
    pointer = { x: e.clientX, y: e.clientY, movedAt: performance.now() / 1000 };
    if (sleeping && !destroyed) resume();
  };
  const onResize = (): void => scene.resize();
  const onVisibility = (): void => {
    if (document.visibilityState === 'hidden') {
      if (!opts.timerLoop) pause();
    } else if (!sleeping) resume();
  };
  window.addEventListener('pointermove', onMove, { passive: true });
  window.addEventListener('resize', onResize);
  document.addEventListener('visibilitychange', onVisibility);

  /* ---- commands from the menu ---- */
  const untake = takeDragonCommands((c) => {
    if (c.kind === 'reset') {
      growth.reset();
      mind.reset();
      egg.mesh.visible = true;
      dragonState.report({ stage: 'egg', age: 0 });
    } else if (c.kind === 'species') {
      const sp = speciesById(c.id);
      if (sp.id !== species.id) {
        void loadDragon(sp).then((ok) => {
          if (ok) growth.setSpecies(sp.id);
        });
      }
    } else if (c.kind === 'hunt') {
      mind.huntNow();
    }
  });

  /* ---- the celebration of a stage-up: a stretch and a puff ---- */
  let popAt = -1e9;
  const celebrate = (): void => {
    popAt = performance.now() / 1000;
    if (dragon) {
      const mouth = dragon.mouthWorld(new Vector3());
      fire.puff(mouth, dragon.forward(new Vector3()), growth.current().fire, popAt);
    }
  };

  /* ---- measurements ---- */
  const intervals: number[] = [];
  const cpuTimes: number[] = [];
  let frames = 0;
  let prevFrameAt = 0;
  let gpuMs: number | null = null;
  let gpuMax: number | null = null;
  let query: WebGLQuery | null = null;
  let queryOpen = false;
  const gl = scene.renderer.getContext() as WebGL2RenderingContext;
  const timer = dev ? (gl.getExtension('EXT_disjoint_timer_query_webgl2') as TimerExt | null) : null;

  const report = (): void => {
    if (!onStats || !dragon) return;
    const sorted = [...intervals].sort((a, b) => a - b);
    const q = (p: number) => (sorted.length ? sorted[Math.floor((sorted.length - 1) * p)] : 0);
    const cpu = [...cpuTimes].sort((a, b) => a - b);
    const memory = (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory;
    onStats({
      species: species.id,
      stage: growth.current().id,
      age: growth.age,
      scale: growth.scale(),
      mood: mind.mood,
      hunger: lastHunger,
      fatigue: lastFatigue,
      burns: burns.count(),
      frames,
      p50: q(0.5),
      p95: q(0.95),
      max: q(1),
      gpuMs,
      gpuMax,
      cpuMs: cpu.length ? cpu[Math.floor((cpu.length - 1) * 0.5)] : 0,
      cpuMax: cpu.length ? cpu[cpu.length - 1] : 0,
      drawCalls: scene.renderer.info.render.calls,
      triangles: scene.renderer.info.render.triangles,
      particles: fire.count(),
      dpr: scene.renderer.getPixelRatio(),
      clips: dragon.clipNames().join(' '),
      heapMb: memory ? memory.usedJSHeapSize / 1048576 : null,
    });
  };

  /* ---- the loop ---- */
  let raf = 0;
  let running = false;
  let sleeping = false;
  let retired = false;
  let last = 0;
  let lastHunger = 0;
  let lastFatigue = 0;
  const pose: Pose = { position: new Vector3(), heading: new Vector3(0, 0, 1), roll: 0, pitch: 0, scale: 0, clip: 'fly', clipSpeed: 1 };
  const hitWorld = new Vector3();
  const mouth = new Vector3();
  const aim = new Vector3();
  let aimTarget: { el: HTMLElement } | null = null;
  let lastReport = 0;

  const tick = (nowMs: number): void => {
    if (!running || destroyed) return;
    const tickStart = dev ? performance.now() : 0;
    // the simulation step is clamped so a stall never teleports the dragon; age counts wall-clock time
    // (capped at a second per tick) so it keeps growing even when frames are slow
    const wall = clamp((nowMs - last) / 1000, 0, 1);
    const dt = clamp(wall, 0.004, 0.05);
    last = nowMs;
    const now = nowMs / 1000;
    frames++;
    if (dev) {
      if (prevFrameAt) {
        intervals.push(nowMs - prevFrameAt);
        if (intervals.length > 300) intervals.shift();
      }
      prevFrameAt = nowMs;
      if (timer && !query) {
        query = gl.createQuery();
        if (query) {
          gl.beginQuery(timer.TIME_ELAPSED_EXT, query);
          queryOpen = true;
        }
      }
    }

    growth.active = !retired;
    growth.tick(wall, nowMs);
    const stage: Stage = growth.current();
    const d = dragon;
    if (!d) {
      schedule();
      return;
    }

    const out = mind.update({
      now,
      dt,
      pointer: retired ? null : pointer,
      stage,
      age: growth.age,
      scale: growth.scale(),
      wingspan: d.wingspanPx() || species.wingspan * growth.scale(),
      world: scene,
      species,
      hasAttack: d.has('attack'),
      burnsFull: retired || burns.count() >= 3,
    });
    lastHunger = out.hunger;
    lastFatigue = out.fatigue;

    for (const e of out.events) {
      switch (e.kind) {
        case 'hatch':
          egg.mesh.visible = false;
          fire.shells(e.at, now);
          break;
        case 'breathe-start': {
          d.mouthWorld(mouth);
          const r = e.target.el.getBoundingClientRect();
          scene.toWorld(r.left + r.width / 2, r.top + r.height / 2, 0, aim);
          aimTarget = e.target;
          fire.breathe(mouth, aim, e.seconds, e.strength, now);
          break;
        }
        case 'ignite': {
          const bleed = stage.scorchBleed;
          burns.ignite(e.target, e.hit, e.strength, bleed, now);
          scene.toWorld(e.hit.x, e.hit.y, 6, hitWorld);
          fire.ignite(hitWorld, e.strength, now);
          break;
        }
        case 'breathe-end':
          fire.stopBreath();
          aimTarget = null;
          break;
        case 'doze':
          sleeping = true;
          break;
        default:
          break;
      }
    }

    // the stage-up pop: a brief stretch
    const sincePop = now - popAt;
    const pop = sincePop < 0.6 ? 1 + 0.14 * Math.sin((sincePop / 0.6) * Math.PI) : 1;

    pose.position.copy(out.position);
    pose.heading.copy(out.heading);
    pose.roll = out.roll;
    pose.pitch = out.pitch;
    pose.scale = growth.scale() * out.scaleMul * pop;
    pose.clip = out.clip;
    pose.clipSpeed = out.clipSpeed;
    d.setPose(pose, dt);
    d.lookAt(out.lookAt, out.lookWeight);
    d.update(dt);

    if (out.eggAt) {
      egg.mesh.visible = true;
      egg.update(growth.age, now, out.eggAt);
    } else {
      egg.mesh.visible = false;
    }

    if (aimTarget && out.mood === 'breathe') {
      d.mouthWorld(mouth);
      const r = aimTarget.el.getBoundingClientRect();
      scene.toWorld(r.left + r.width / 2, r.top + r.height / 2, 0, aim);
      fire.follow(mouth, aim);
    }
    fire.update(dt, now);
    burns.update(now);
    scene.tint(now);
    scene.render();

    if (dev) {
      cpuTimes.push(performance.now() - tickStart);
      if (cpuTimes.length > 300) cpuTimes.shift();
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
      if (now - lastReport > 0.25) {
        lastReport = now;
        report();
      }
    }
    if (now - lastStateAt > 1) {
      lastStateAt = now;
      dragonState.report({ stage: stage.id, age: growth.age, species: species.id });
    }

    if (sleeping) {
      running = false;
      growth.save();
      return;
    }
    schedule();
  };
  let lastStateAt = 0;

  // dev: a timer-driven loop for environments that throttle requestAnimationFrame (the in-app browser pane)
  const schedule = (): void => {
    if (opts.timerLoop) raf = window.setTimeout(() => tick(performance.now()), 16);
    else raf = requestAnimationFrame(tick);
  };
  const pause = (): void => {
    if (!running) return;
    running = false;
    if (opts.timerLoop) window.clearTimeout(raf);
    else cancelAnimationFrame(raf);
    growth.save();
  };
  const resume = (): void => {
    sleeping = false;
    if (running || destroyed) return;
    running = true;
    last = performance.now();
    schedule();
  };

  dragonState.report({ stage: growth.current().id, age: growth.age, species: species.id, ready: true });
  // a hidden tab waits for visibilitychange; the dev timer loop runs regardless (the pane reports hidden)
  if (document.visibilityState !== 'hidden' || opts.timerLoop) resume();

  if (dev) {
    // a probe for the dev console: does the model fly head first? (+ = the head leads the heading)
    (window as unknown as { __dragon?: unknown }).__dragon = {
      probe: () => {
        const d = dragon;
        if (!d) return null;
        const head = d.mouthWorld(new Vector3()).sub(d.root.position);
        const fwd = d.forward(new Vector3());
        return { mood: mind.mood, facing: head.dot(fwd) / Math.max(1, head.length()), speed: mind.vel.length(), pos: d.root.position.toArray() };
      },
    };
  }

  return {
    retire() {
      retired = true;
      fire.stopBreath();
      window.setTimeout(() => pause(), 600);
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      pause();
      untake();
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('resize', onResize);
      document.removeEventListener('visibilitychange', onVisibility);
      if (query) gl.deleteQuery(query);
      growth.save();
      burns.dispose();
      fire.dispose();
      dragon?.dispose();
      egg.dispose();
      scene.dispose();
      dragonState.report({ ready: false });
    },
  };
}
