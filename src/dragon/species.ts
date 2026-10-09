/*
 * A dragon is a model plus a species file. Everything that differs between dragons lives here: where the
 * glTF is, which animation clips mean fly / glide / idle / attack (matched by name, case-insensitively),
 * where the mouth and head are, how big an adult is, and a temperament the mind multiplies its urges by.
 * Adding a dragon is adding a folder under public/dragons/ and an entry below.
 */

export interface Temperament {
  /** How much the pointer interests it (0..1). */
  curiosity: number;
  /** How quickly hunger turns into a hunt (0..1). */
  aggression: number;
  /** How soon it tires of perching and changes course (0..1). */
  restlessness: number;
}

export interface Species {
  id: string;
  name: string;
  /** Shown in the menu: model title, author, licence. */
  credit: string;
  creditUrl: string;
  /** Same-origin path to the glTF or glb. */
  model: string;
  /** Wingspan of an adult in CSS px (the model is normalised to it). */
  wingspan: number;
  /** The axis the model faces in its own space; the creature is turned to face +Z before steering. */
  forward: 'z' | '-z' | 'x' | '-x';
  /** Clip names, matched case-insensitively against the glTF's animations. */
  clips: {
    fly: RegExp;
    glide?: RegExp;
    idle: RegExp;
    attack?: RegExp;
  };
  /** Bone names (regex) for the head and, if the rig has one, the jaw or mouth. */
  head?: RegExp;
  mouth?: RegExp;
  /**
   * Where the mouth is when no mouth bone is found, as a fraction of the normalised body: x to the right,
   * y up, z forward, each in units of the wingspan.
   */
  mouthOffset: [number, number, number];
  /** Playback rate of the flight loop at cruise. */
  clipSpeed: number;
  temperament: Temperament;
}

export const SPECIES: Record<string, Species> = {
  lowpoly: {
    id: 'lowpoly',
    name: 'Low-poly',
    credit: '"Low Poly Dragon" by bocdagla, CC BY',
    creditUrl: 'https://bocdagla.itch.io/low-poly-dragon',
    model: '/dragons/lowpoly/dragon.glb',
    wingspan: 300,
    forward: 'z',
    // clips in the file: Attack, AttackPose, Flying_loop, GroundToFly, Idle_loop, Walk_menacing_loop
    clips: { fly: /^Flying_loop$/i, idle: /^Idle_loop$/i, attack: /^Attack$/i },
    head: /^Head$/,
    mouth: /^Head\.Tip$/,
    mouthOffset: [0, 0.02, 0.42],
    clipSpeed: 1,
    temperament: { curiosity: 0.9, aggression: 0.4, restlessness: 0.8 },
  },
  wyvern: {
    id: 'wyvern',
    name: 'Wyvern',
    credit: '"Animated Dragon Three Motion Loops" by LasquetiSpice, CC BY 4.0',
    creditUrl: 'https://sketchfab.com/3d-models/animated-dragon-three-motion-loops-eca98cf6cd084c1596cecf716e110c29',
    model: '/dragons/wyvern/scene.gltf',
    wingspan: 340,
    forward: 'z',
    clips: { fly: /fly|flap|wing|take|^(?!.*idle).*$/i, glide: /glide|soar|hover/i, idle: /idle|rest|breath/i },
    head: /head/i,
    mouth: /jaw|mouth/i,
    mouthOffset: [0, 0, 0.45],
    clipSpeed: 1,
    temperament: { curiosity: 0.7, aggression: 0.6, restlessness: 0.5 },
  },
};

export const DEFAULT_SPECIES = 'lowpoly';

export function speciesById(id: string | undefined): Species {
  return (id && SPECIES[id]) || SPECIES[DEFAULT_SPECIES];
}
