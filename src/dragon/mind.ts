/*
 * The mind: what the dragon does and why. Every 0.8–3 s it scores soaring, curiosity, perching and
 * hunting from what has happened (how long it has flown, how hungry it is, whether you moved, what on the
 * page it has not burned yet), multiplies by the species' temperament, and picks one with a little
 * randomness. Movement is steering in world px: wander, seek, arrive, soft bounds; the body banks from
 * the lateral force and pitches from the climb. No three.js scene access here — it returns a pose and
 * events, and the engine renders them.
 */
import { Vector3 } from 'three';
import type { Stage } from './growth';
import type { Species } from './species';
import { flammable, perches, pickTarget } from './targets';
import type { BurnMemory, Target } from './targets';
import type { ClipName } from './dragon';

export type Mood = 'egg' | 'hatching' | 'soar' | 'curious' | 'perch' | 'perched' | 'hunt' | 'breathe' | 'satisfied' | 'doze';

export interface Pointer {
  x: number;
  y: number;
  /** Seconds (performance.now() / 1000) of the last move. */
  movedAt: number;
}

export interface World {
  width: number;
  height: number;
  toWorld(x: number, y: number, z?: number, into?: Vector3): Vector3;
  toScreen(v: Vector3): { x: number; y: number };
  bounds(): { hx: number; hy: number; zMin: number; zMax: number };
}

export interface MindInput {
  now: number;
  dt: number;
  pointer: Pointer | null;
  stage: Stage;
  /** Age in seconds. */
  age: number;
  /** Growth scale 0..1.3. */
  scale: number;
  /** Wingspan in px at this scale. */
  wingspan: number;
  world: World;
  species: Species;
  hasAttack: boolean;
  /** True when the burn layer cannot take another scorch. */
  burnsFull: boolean;
}

export type MindEvent =
  | { kind: 'hatch'; at: Vector3 }
  | { kind: 'breathe-start'; seconds: number; strength: number; target: Target }
  | { kind: 'ignite'; target: Target; hit: { x: number; y: number }; strength: number }
  | { kind: 'breathe-end' }
  | { kind: 'perched' }
  | { kind: 'takeoff' }
  | { kind: 'doze' }
  | { kind: 'wake' }
  | { kind: 'stage'; stage: Stage };

export interface MindOutput {
  mood: Mood;
  position: Vector3;
  heading: Vector3;
  roll: number;
  pitch: number;
  /** Multiplier on the growth scale (0 while an egg, rising through the hatch). */
  scaleMul: number;
  clip: ClipName;
  clipSpeed: number;
  lookAt: Vector3 | null;
  lookWeight: number;
  /** Where the egg sits while there is one, world px. */
  eggAt: Vector3 | null;
  events: MindEvent[];
  hunger: number;
  fatigue: number;
}

const HATCH_AGE = 20;
const HATCH_SECONDS = 1.6;
const CURIOUS_FADE = 6;
const DOZE_AFTER = 180;
const MARGIN = 56;

const tmp = new Vector3();
const tmp2 = new Vector3();
const tmp3 = new Vector3();

export class Mind {
  mood: Mood = 'egg';
  readonly pos = new Vector3();
  readonly vel = new Vector3();
  private readonly heading = new Vector3(0, 0, 1);
  private roll = 0;
  private pitch = 0;
  private moodSince = 0;
  private decisionAt = 0;
  private readonly wander = new Vector3();
  private wanderAt = -1e9;
  private target: Target | null = null;
  private readonly standoff = new Vector3();
  private breathUntil = 0;
  private igniteAt = 0;
  private ignited = false;
  private breathStrength = 1;
  private readonly perchPoint = new Vector3();
  private perchedUntil = 0;
  private readonly eggPoint = new Vector3();
  private hatchStart = 0;
  private hunger = 0;
  private fatigue = 0;
  private curiousUntil = 0;
  private orbit = 0;
  private lastStage: Stage | null = null;
  private dozing = false;
  readonly memory: BurnMemory = new Map();
  private readonly events: MindEvent[] = [];
  private readonly lookTarget = new Vector3();
  private readonly out: MindOutput;

  constructor(private readonly rnd: () => number = Math.random) {
    this.out = {
      mood: 'egg', position: this.pos, heading: this.heading, roll: 0, pitch: 0, scaleMul: 0, clip: 'fly', clipSpeed: 1,
      lookAt: null, lookWeight: 0, eggAt: null, events: this.events, hunger: 0, fatigue: 0,
    };
  }

  /** Dev: breathe on the next decision. */
  huntNow(): void {
    this.hunger = 1;
    this.decisionAt = 0;
    if (this.mood === 'perched' || this.mood === 'doze') this.takeOff(0);
  }

  /** A new egg (after a reset). */
  reset(): void {
    this.mood = 'egg';
    this.vel.set(0, 0, 0);
    this.hunger = 0;
    this.fatigue = 0;
    this.memory.clear();
    this.lastStage = null;
  }

  private setMood(mood: Mood, now: number): void {
    this.mood = mood;
    this.moodSince = now;
  }

  private emit(e: MindEvent): void {
    this.events.push(e);
  }

  private pointerWorld(input: MindInput, z = 40): Vector3 | null {
    if (!input.pointer) return null;
    return input.world.toWorld(input.pointer.x, input.pointer.y, z, tmp3);
  }

  private pointerRecent(input: MindInput): boolean {
    return !!input.pointer && input.now - input.pointer.movedAt < CURIOUS_FADE;
  }

  private eggSpot(input: MindInput): Vector3 {
    const spots = perches();
    const panel = spots.find((p) => p.el.classList.contains('ob-form')) ?? spots[0];
    if (panel) {
      const r = panel.rect;
      return input.world.toWorld(r.left + 52, r.top + 2, 0, this.eggPoint);
    }
    return input.world.toWorld(input.world.width * 0.3, input.world.height * 0.62, 0, this.eggPoint);
  }

  private pickWander(input: MindInput): void {
    const b = input.world.bounds();
    this.wander.set((this.rnd() * 2 - 1) * b.hx, (this.rnd() * 2 - 1) * b.hy, b.zMin + this.rnd() * (b.zMax - b.zMin));
    this.wanderAt = input.now;
  }

  private decide(input: MindInput): void {
    const t = input.species.temperament;
    const now = input.now;
    const scores: [Mood, number][] = [['soar', 1]];
    if (this.pointerRecent(input)) scores.push(['curious', 1.5 * t.curiosity]);
    const spots = perches();
    if (spots.length && this.fatigue > 0.35) scores.push(['perch', this.fatigue * 1.3 * (1 - 0.5 * t.restlessness)]);
    if (this.hunger > 0.5 && !input.burnsFull) {
      const targets = flammable();
      const pick = pickTarget(targets, { pointer: input.pointer, memory: this.memory, now, random: this.rnd });
      if (pick) {
        scores.push(['hunt', this.hunger * 2.2 * (0.4 + t.aggression)]);
        this.target = pick;
      }
    }
    let best: Mood = 'soar';
    let bestScore = -1;
    for (const [mood, score] of scores) {
      const s = score * (0.75 + 0.5 * this.rnd());
      if (s > bestScore) {
        bestScore = s;
        best = mood;
      }
    }
    this.decisionAt = now + 0.8 + this.rnd() * 2.2;
    if (best === this.mood) return;
    switch (best) {
      case 'hunt': {
        this.beginHunt(input);
        break;
      }
      case 'perch': {
        const spot = spots.find((p) => p.el.classList.contains('ob-form')) ?? spots[0];
        const r = spot.rect;
        input.world.toWorld(r.left + (0.2 + 0.6 * this.rnd()) * r.width, r.top + 2, 0, this.perchPoint);
        this.perchPoint.y += input.wingspan * 0.12;
        this.setMood('perch', now);
        break;
      }
      case 'curious': {
        this.curiousUntil = now + 8 + this.rnd() * 6;
        this.setMood('curious', now);
        break;
      }
      default:
        this.pickWander(input);
        this.setMood('soar', now);
    }
  }

  private beginHunt(input: MindInput): void {
    const t = this.target;
    if (!t) return;
    const r = t.rect;
    const centre = input.world.toWorld(r.left + r.width / 2, r.top + r.height / 2, 0, tmp);
    const side = this.pos.x > centre.x ? 1 : -1;
    this.standoff.set(centre.x + side * input.wingspan * 1.1, centre.y + input.wingspan * 0.3, 70);
    const b = input.world.bounds();
    this.standoff.x = Math.max(-b.hx, Math.min(b.hx, this.standoff.x));
    this.standoff.y = Math.max(-b.hy, Math.min(b.hy, this.standoff.y));
    this.setMood('hunt', input.now);
  }

  private takeOff(now: number): void {
    this.vel.set((this.rnd() - 0.5) * 240, 220, 120);
    this.fatigue = 0;
    this.pickWanderLater = true;
    this.setMood('soar', now);
    this.emit({ kind: 'takeoff' });
  }
  private pickWanderLater = false;

  update(input: MindInput): MindOutput {
    const { now, dt } = input;
    this.events.length = 0;
    const out = this.out;

    if (this.lastStage && input.stage !== this.lastStage && input.stage.id !== 'egg') this.emit({ kind: 'stage', stage: input.stage });
    this.lastStage = input.stage;

    // the egg and the hatch
    if (input.age < HATCH_AGE) {
      if (this.mood !== 'egg') this.setMood('egg', now);
      const spot = this.eggSpot(input);
      this.pos.copy(spot);
      this.vel.set(0, 0, 0);
      out.eggAt = spot;
      out.scaleMul = 0;
      return this.finish(input, 'idle', 1, null, 0);
    }
    if (this.mood === 'egg') {
      this.setMood('hatching', now);
      this.hatchStart = now;
      const spot = this.eggSpot(input);
      this.pos.copy(spot).add(tmp.set(0, input.wingspan * 0.15, 20));
      this.emit({ kind: 'hatch', at: spot.clone() });
    }
    if (this.mood === 'hatching') {
      const t = Math.min(1, (now - this.hatchStart) / HATCH_SECONDS);
      out.eggAt = null;
      out.scaleMul = t * t * (3 - 2 * t);
      this.heading.set(0, 0.2, 1).normalize();
      if (t >= 1) this.takeOff(now);
      return this.finish(input, 'idle', 1, this.pointerWorld(input), 0.6);
    }
    out.eggAt = null;
    out.scaleMul = 1;

    // drives
    const aggression = 0.6 + 0.8 * input.species.temperament.aggression;
    this.hunger = Math.min(1, this.hunger + (dt / input.stage.hungerFill) * aggression);
    const flying = this.mood === 'soar' || this.mood === 'curious' || this.mood === 'hunt';
    if (flying) this.fatigue = Math.min(1, this.fatigue + dt / 45);

    // sleep and waking
    const idle = input.pointer ? now - input.pointer.movedAt : Infinity;
    if (this.mood === 'perched' && idle > DOZE_AFTER && !this.dozing) {
      this.dozing = true;
      this.setMood('doze', now);
      this.emit({ kind: 'doze' });
    }
    if (this.mood === 'doze') {
      if (idle < 1) {
        this.dozing = false;
        this.emit({ kind: 'wake' });
        this.takeOff(now);
      } else {
        return this.finish(input, 'idle', 0.5, null, 0);
      }
    }

    if (now >= this.decisionAt && (this.mood === 'soar' || this.mood === 'curious')) this.decide(input);

    const maxSpeed = input.stage.cruise * (this.mood === 'curious' ? 0.8 : 1);
    const maxForce = maxSpeed * 2.2;
    const force = tmp2.set(0, 0, 0);
    let clip: ClipName = 'fly';
    let clipSpeed = 1;
    let look: Vector3 | null = null;
    let lookWeight = 0;
    let hold = false;

    switch (this.mood) {
      case 'soar': {
        if (this.pickWanderLater || now - this.wanderAt > 3.2 || this.pos.distanceTo(this.wander) < 70) {
          this.pickWander(input);
          this.pickWanderLater = false;
        }
        this.seek(this.wander, maxSpeed, maxForce, force);
        break;
      }
      case 'curious': {
        const p = this.pointerWorld(input, 40);
        if (!p || !this.pointerRecent(input) || now > this.curiousUntil) {
          this.pickWander(input);
          this.setMood('soar', now);
          break;
        }
        this.orbit += dt * 0.9;
        const r = 160 + input.wingspan * 0.5;
        tmp.set(p.x + Math.cos(this.orbit) * r, p.y + Math.sin(this.orbit * 0.7) * r * 0.5, 30);
        this.seek(tmp, maxSpeed, maxForce, force);
        look = p;
        lookWeight = 0.8;
        break;
      }
      case 'perch': {
        this.arrive(this.perchPoint, maxSpeed, maxForce, force, 180);
        if (this.pos.distanceTo(this.perchPoint) < 14 && this.vel.length() < 90) {
          this.pos.copy(this.perchPoint);
          this.vel.set(0, 0, 0);
          const t = input.species.temperament;
          this.perchedUntil = now + 6 + (1 - t.restlessness) * 10 + this.rnd() * 6;
          this.setMood('perched', now);
          this.emit({ kind: 'perched' });
        }
        clip = 'glide';
        break;
      }
      case 'perched': {
        hold = true;
        clip = 'idle';
        clipSpeed = 1;
        this.fatigue = Math.max(0, this.fatigue - dt / 12);
        const p = this.pointerWorld(input, 40);
        if (p && this.pointerRecent(input)) {
          look = p;
          lookWeight = 0.9;
          this.heading.set(p.x - this.pos.x, 0, 1).normalize();
        } else {
          this.heading.set(0.3, 0.05, 1).normalize();
        }
        if (now > this.perchedUntil || (this.hunger > 0.9 && now - this.moodSince > 3)) this.takeOff(now);
        break;
      }
      case 'hunt': {
        const t = this.target;
        if (!t || !t.el.isConnected) {
          this.setMood('soar', now);
          break;
        }
        this.arrive(this.standoff, maxSpeed, maxForce, force, 220);
        clip = 'fly';
        const centre = input.world.toWorld(t.rect.left + t.rect.width / 2, t.rect.top + t.rect.height / 2, 0, tmp);
        look = centre.clone();
        lookWeight = 0.7;
        if (this.pos.distanceTo(this.standoff) < 30 && this.vel.length() < 110) {
          const [a, b] = input.stage.breath;
          const seconds = a + this.rnd() * (b - a);
          this.breathUntil = now + seconds;
          this.igniteAt = now + 0.4;
          this.ignited = false;
          this.breathStrength = input.stage.fire;
          this.setMood('breathe', now);
          this.emit({ kind: 'breathe-start', seconds, strength: this.breathStrength, target: t });
        } else if (now - this.moodSince > 9) {
          // could not get there: give up gracefully
          this.hunger *= 0.5;
          this.setMood('soar', now);
        }
        break;
      }
      case 'breathe': {
        hold = true;
        const t = this.target;
        if (t) {
          const r = t.el.getBoundingClientRect();
          const centre = input.world.toWorld(r.left + r.width / 2, r.top + r.height / 2, 0, tmp);
          this.heading.copy(centre).sub(this.pos).normalize();
          look = centre.clone();
          lookWeight = 1;
          if (!this.ignited && now >= this.igniteAt) {
            this.ignited = true;
            this.memory.set(t.key, now);
            this.emit({
              kind: 'ignite',
              target: t,
              hit: { x: r.left + r.width * (0.3 + 0.4 * this.rnd()), y: r.top + r.height * (0.35 + 0.3 * this.rnd()) },
              strength: this.breathStrength,
            });
          }
        }
        clip = input.hasAttack ? 'attack' : 'fly';
        clipSpeed = 1.3;
        // a slow bob while hovering
        this.pos.y += Math.sin(now * 5) * 0.25;
        if (now >= this.breathUntil) {
          this.emit({ kind: 'breathe-end' });
          this.hunger = 0;
          this.setMood('satisfied', now);
          const away = tmp.copy(this.pos).sub(this.standoff).setZ(0);
          this.vel.set((this.pos.x >= 0 ? -1 : 1) * 260 + away.x, 180, -160);
        }
        break;
      }
      case 'satisfied': {
        clip = 'fly';
        clipSpeed = 1.2;
        const t = (now - this.moodSince) / 0.9;
        this.roll = Math.sin(Math.min(1, t) * Math.PI) * 1.1 * (this.pos.x >= 0 ? -1 : 1);
        this.pickWander(input);
        this.seek(this.wander, maxSpeed, maxForce, force);
        if (t >= 1) this.setMood('soar', now);
        break;
      }
      default:
        break;
    }

    // soft bounds: pushed back when outside the volume
    const b = input.world.bounds();
    if (!hold) {
      const push = (v: number, lo: number, hi: number) => (v < lo ? (lo - v) * 4 : v > hi ? (hi - v) * 4 : 0);
      force.x += push(this.pos.x, -b.hx, b.hx);
      force.y += push(this.pos.y, -b.hy, b.hy);
      force.z += push(this.pos.z, b.zMin, b.zMax) * 2;
    }

    // integrate
    if (hold) {
      this.vel.multiplyScalar(Math.exp(-dt / 0.12));
    } else {
      const f = force.length();
      if (f > maxForce) force.multiplyScalar(maxForce / f);
      this.vel.addScaledVector(force, dt);
      const s = this.vel.length();
      if (s > maxSpeed) this.vel.multiplyScalar(maxSpeed / s);
      this.pos.addScaledVector(this.vel, dt);
      // heading, bank and pitch from the motion
      if (s > 20) {
        this.heading.copy(this.vel).normalize();
        const lateral = this.vel.x * force.y - this.vel.y * force.x; // z of vel × force, in the screen plane
        const targetRoll = Math.max(-0.8, Math.min(0.8, (lateral / (s * maxForce)) * 1.2));
        if (this.mood !== 'satisfied') this.roll += (targetRoll - this.roll) * (1 - Math.exp(-dt / 0.35));
        this.pitch += (Math.asin(Math.max(-1, Math.min(1, this.vel.y / s))) * 0.5 - this.pitch) * (1 - Math.exp(-dt / 0.3));
      }
      if (clip === 'fly' && this.mood === 'soar' && s > maxSpeed * 0.72 && Math.abs(this.vel.y) < s * 0.25) clip = 'glide';
      clipSpeed = 0.75 + (s / Math.max(1, maxSpeed)) * 0.7;
    }
    if (hold) {
      this.roll += (0 - this.roll) * (1 - Math.exp(-dt / 0.3));
      this.pitch += (0 - this.pitch) * (1 - Math.exp(-dt / 0.3));
    }

    return this.finish(input, clip, clipSpeed, look, lookWeight);
  }

  private finish(input: MindInput, clip: ClipName, clipSpeed: number, look: Vector3 | null, lookWeight: number): MindOutput {
    const out = this.out;
    out.mood = this.mood;
    out.roll = this.roll;
    out.pitch = this.pitch;
    out.clip = clip;
    out.clipSpeed = clipSpeed * input.species.clipSpeed;
    if (look) this.lookTarget.copy(look);
    out.lookAt = look ? this.lookTarget : null;
    out.lookWeight = lookWeight;
    out.hunger = this.hunger;
    out.fatigue = this.fatigue;
    return out;
  }

  private seek(target: Vector3, maxSpeed: number, maxForce: number, into: Vector3): void {
    const desired = tmp.copy(target).sub(this.pos);
    const d = desired.length();
    if (d < 1e-3) return;
    desired.multiplyScalar(maxSpeed / d).sub(this.vel);
    const f = desired.length();
    if (f > maxForce) desired.multiplyScalar(maxForce / f);
    into.add(desired);
  }

  private arrive(target: Vector3, maxSpeed: number, maxForce: number, into: Vector3, slowRadius: number): void {
    const desired = tmp.copy(target).sub(this.pos);
    const d = desired.length();
    if (d < 1e-3) return;
    const speed = d < slowRadius ? maxSpeed * (d / slowRadius) : maxSpeed;
    desired.multiplyScalar(speed / d).sub(this.vel);
    const f = desired.length();
    if (f > maxForce) desired.multiplyScalar(maxForce / f);
    into.add(desired);
  }
}
