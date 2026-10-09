/*
 * Growth: the dragon's age and what it means. Age accrues only while Home is the view and the tab is
 * visible, and is remembered in localStorage. Very fast for the POC: egg to elder in nine minutes.
 * Everything that scales with age (size, speed, hunger, fire) is read from the stage table here, so a
 * species never needs to know how old the creature is.
 */

export type StageId = 'egg' | 'hatchling' | 'fledgling' | 'young' | 'adult' | 'elder';

export interface Stage {
  id: StageId;
  /** Age in seconds at which the stage begins. */
  from: number;
  /** Model scale relative to the species' adult size. */
  scale: number;
  /** Cruise speed in CSS px per second. */
  cruise: number;
  /** Seconds for hunger to fill from 0 to 1. */
  hungerFill: number;
  /** Seconds of breath, min and max. */
  breath: [number, number];
  /** Multiplier on the breath's particle rate and size. */
  fire: number;
  /** Extra px the scorch spreads beyond the target's box. */
  scorchBleed: number;
}

export const STAGES: readonly Stage[] = [
  { id: 'egg', from: 0, scale: 0, cruise: 0, hungerFill: Infinity, breath: [0, 0], fire: 0, scorchBleed: 0 },
  { id: 'hatchling', from: 20, scale: 0.3, cruise: 180, hungerFill: 60, breath: [0.6, 0.8], fire: 0.3, scorchBleed: 8 },
  { id: 'fledgling', from: 90, scale: 0.5, cruise: 240, hungerFill: 50, breath: [0.9, 1.1], fire: 0.6, scorchBleed: 14 },
  { id: 'young', from: 210, scale: 0.75, cruise: 320, hungerFill: 40, breath: [1.1, 1.4], fire: 0.85, scorchBleed: 20 },
  { id: 'adult', from: 360, scale: 1, cruise: 380, hungerFill: 32, breath: [1.3, 1.7], fire: 1.1, scorchBleed: 26 },
  { id: 'elder', from: 540, scale: 1.3, cruise: 420, hungerFill: 25, breath: [1.5, 1.8], fire: 1.4, scorchBleed: 32 },
];

export const STORAGE_KEY = 'tayyari.dragon';

export interface GrowthRecord {
  /** Age in seconds. */
  age: number;
  species: string;
}

export function stageFor(age: number): Stage {
  let stage = STAGES[0];
  for (const s of STAGES) if (age >= s.from) stage = s;
  return stage;
}

function nextStage(stage: Stage): Stage | undefined {
  const i = STAGES.indexOf(stage);
  return STAGES[i + 1];
}

/** 0..1 through the current stage (1 forever in the last). */
export function stageProgress(age: number): number {
  const stage = stageFor(age);
  const next = nextStage(stage);
  if (!next) return 1;
  return Math.min(1, Math.max(0, (age - stage.from) / (next.from - stage.from)));
}

const smooth = (t: number): number => t * t * (3 - 2 * t);

/** The model scale right now: eases from the stage's scale to the next one's, so growth is continuous. */
export function scaleFor(age: number): number {
  const stage = stageFor(age);
  const next = nextStage(stage);
  if (stage.id === 'egg') return 0;
  if (!next) return stage.scale;
  return stage.scale + (next.scale - stage.scale) * smooth(stageProgress(age));
}

/** "2:14" for the chip. */
export function ageLabel(age: number): string {
  const m = Math.floor(age / 60);
  const s = Math.floor(age % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

function read(): GrowthRecord | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<GrowthRecord>;
    if (typeof v.age !== 'number' || !Number.isFinite(v.age) || v.age < 0) return null;
    return { age: v.age, species: typeof v.species === 'string' ? v.species : 'lowpoly' };
  } catch {
    return null;
  }
}

function write(rec: GrowthRecord): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(rec));
  } catch {
    /* storage blocked: the dragon still grows, it just forgets on reload */
  }
}

export interface GrowthOptions {
  /** Called when the stage changes, after the age has crossed its threshold. */
  onStage?: (stage: Stage, previous: Stage) => void;
  /** Dev: start at this age (`?dragon=age:600`). */
  startAge?: number;
  defaultSpecies: string;
}

/** The clock of the creature's life. `tick` is called from the frame loop; it only counts while `active`. */
export class Growth {
  age: number;
  species: string;
  active = true;
  private stage: Stage;
  private lastSave = 0;
  private readonly onStage: GrowthOptions['onStage'];

  constructor(opts: GrowthOptions) {
    const saved = read();
    this.age = opts.startAge ?? saved?.age ?? 0;
    this.species = saved?.species ?? opts.defaultSpecies;
    this.stage = stageFor(this.age);
    this.onStage = opts.onStage;
  }

  current(): Stage {
    return this.stage;
  }

  scale(): number {
    return scaleFor(this.age);
  }

  tick(dt: number, now: number): void {
    if (!this.active) return;
    this.age += dt;
    const stage = stageFor(this.age);
    if (stage !== this.stage) {
      const previous = this.stage;
      this.stage = stage;
      this.onStage?.(stage, previous);
    }
    if (now - this.lastSave > 5000) {
      this.lastSave = now;
      write({ age: this.age, species: this.species });
    }
  }

  setAge(age: number): void {
    this.age = Math.max(0, age);
    this.stage = stageFor(this.age);
    write({ age: this.age, species: this.species });
  }

  setSpecies(species: string): void {
    this.species = species;
    write({ age: this.age, species });
  }

  /** A new egg. */
  reset(): void {
    this.setAge(0);
  }

  save(): void {
    write({ age: this.age, species: this.species });
  }
}
