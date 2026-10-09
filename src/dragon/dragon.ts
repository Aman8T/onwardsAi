/*
 * The creature: a species' glTF loaded, normalised (centred, turned to face +Z, scaled so an adult's
 * wingspan is the species' px figure), with its animation clips mapped by name and its head and mouth
 * found in the rig. The mind drives it through `setPose`: position in world px, heading, roll and pitch,
 * growth scale, and which clip to play. Everything here is three.js only; no DOM, no React.
 */
import { AnimationMixer, Box3, Group, LoopRepeat, MathUtils, Object3D, Quaternion, Vector3 } from 'three';
import type { AnimationAction, AnimationClip, Material, Mesh } from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import type { Species } from './species';

export type ClipName = 'fly' | 'glide' | 'idle' | 'attack';

const UP = new Vector3(0, 1, 0);
const tmpQ = new Quaternion();
const tmpM = new Object3D();
const tmpV = new Vector3();

export interface Pose {
  /** World px (x right, y up, z depth). */
  position: Vector3;
  /** Direction of travel, world; zero when hovering (keeps the last heading). */
  heading: Vector3;
  /** Bank around the body's forward axis, radians, positive = right wing down. */
  roll: number;
  /** Nose up (+) or down (−), radians. */
  pitch: number;
  /** Growth scale, 0..1.3 (0 hides the body). */
  scale: number;
  clip: ClipName;
  /** Playback rate of the current clip. */
  clipSpeed: number;
}

export class Dragon {
  /** Positioned and oriented by the pose. */
  readonly root = new Group();
  /** Rolls and pitches inside the root. */
  readonly body = new Group();
  private readonly model: Group;
  private readonly mixer: AnimationMixer;
  private readonly actions = new Map<ClipName, AnimationAction>();
  private current: ClipName | null = null;
  private readonly head: Object3D | null;
  private readonly mouth: Object3D;
  /** Model units → px at scale 1. */
  private readonly unit: number;
  private readonly headRest = new Quaternion();
  private lookWeight = 0;
  private readonly lookTarget = new Vector3();
  private readonly headingQ = new Quaternion();

  private constructor(readonly species: Species, model: Group, clips: AnimationClip[]) {
    this.model = model;
    // normalise: centre on the bounding box, scale by wingspan, face +Z
    const box = new Box3().setFromObject(model);
    const size = box.getSize(new Vector3());
    const centre = box.getCenter(new Vector3());
    const span = Math.max(size.x, size.z, 1e-3);
    this.unit = species.wingspan / span;
    const inner = new Group();
    inner.add(model);
    model.position.sub(centre);
    const turn = { z: 0, '-z': Math.PI, x: Math.PI / 2, '-x': -Math.PI / 2 }[species.forward];
    inner.rotation.y = turn;
    inner.scale.setScalar(this.unit);
    this.body.add(inner);
    this.root.add(this.body);

    this.mixer = new AnimationMixer(model);
    const find = (re: RegExp | undefined): AnimationClip | undefined => (re ? clips.find((c) => re.test(c.name)) : undefined);
    const map: [ClipName, AnimationClip | undefined][] = [
      ['fly', find(species.clips.fly) ?? clips[0]],
      ['glide', find(species.clips.glide)],
      ['idle', find(species.clips.idle)],
      ['attack', find(species.clips.attack)],
    ];
    for (const [name, clip] of map) {
      if (!clip) continue;
      const action = this.mixer.clipAction(clip);
      action.setLoop(LoopRepeat, Infinity);
      action.enabled = true;
      this.actions.set(name, action);
    }

    this.head = species.head ? this.findNode(species.head) : null;
    if (this.head) this.headRest.copy(this.head.quaternion);
    const mouthBone = species.mouth ? this.findNode(species.mouth) : null;
    if (mouthBone) {
      this.mouth = mouthBone;
    } else {
      // an empty at the species' offset, in the normalised body (units of the wingspan)
      this.mouth = new Object3D();
      const [ox, oy, oz] = species.mouthOffset;
      this.mouth.position.set(ox * species.wingspan, oy * species.wingspan, oz * species.wingspan);
      this.body.add(this.mouth);
    }
    for (const mesh of this.meshes()) {
      mesh.frustumCulled = false; // skinned meshes with large wings get culled wrongly at the viewport's edge
    }
  }

  static async load(species: Species): Promise<Dragon> {
    const gltf = await new GLTFLoader().loadAsync(species.model);
    return new Dragon(species, gltf.scene, gltf.animations);
  }

  /** Clip names the model actually has, for the dev read-out and species files. */
  clipNames(): string[] {
    return [...this.actions.keys()];
  }

  has(clip: ClipName): boolean {
    return this.actions.has(clip);
  }

  private findNode(re: RegExp): Object3D | null {
    let found: Object3D | null = null;
    this.model.traverse((o) => {
      if (!found && re.test(o.name)) found = o;
    });
    return found;
  }

  private meshes(): Mesh[] {
    const out: Mesh[] = [];
    this.model.traverse((o) => {
      if ((o as Mesh).isMesh) out.push(o as Mesh);
    });
    return out;
  }

  /** Where the fire comes from, world px. */
  mouthWorld(into = new Vector3()): Vector3 {
    return this.mouth.getWorldPosition(into);
  }

  /** Forward direction of the body, world. */
  forward(into = new Vector3()): Vector3 {
    return into.set(0, 0, 1).applyQuaternion(this.root.quaternion);
  }

  /** Wingspan in px at the current scale. */
  wingspanPx(): number {
    return this.species.wingspan * this.root.scale.x;
  }

  /** Aims the head at a world point (0 = animation only, 1 = fully turned); eased by the caller. */
  lookAt(target: Vector3 | null, weight: number): void {
    if (target) this.lookTarget.copy(target);
    this.lookWeight = target ? weight : 0;
  }

  play(clip: ClipName, speed = 1, fade = 0.3): void {
    const next = this.actions.get(clip) ?? this.actions.get('fly');
    if (!next) return;
    next.timeScale = speed;
    const name: ClipName = this.actions.get(clip) ? clip : 'fly';
    if (this.current === name) return;
    const prev = this.current ? this.actions.get(this.current) : undefined;
    next.reset().play();
    if (prev && prev !== next) prev.crossFadeTo(next, fade, false);
    this.current = name;
  }

  setPose(pose: Pose, dt: number): void {
    this.root.position.copy(pose.position);
    this.root.scale.setScalar(pose.scale);
    this.root.visible = pose.scale > 0.001;
    if (pose.heading.lengthSq() > 1e-6) {
      tmpM.position.set(0, 0, 0);
      tmpM.lookAt(pose.heading);
      this.headingQ.copy(tmpM.quaternion);
    }
    // turn towards the heading over ~0.25 s; big dragons turn slower
    const k = 1 - Math.exp(-dt / (0.18 + 0.12 * pose.scale));
    this.root.quaternion.slerp(this.headingQ, k);
    const kb = 1 - Math.exp(-dt / 0.2);
    this.body.rotation.z = MathUtils.lerp(this.body.rotation.z, -pose.roll, kb);
    this.body.rotation.x = MathUtils.lerp(this.body.rotation.x, -pose.pitch, kb);
    this.play(pose.clip, pose.clipSpeed);
  }

  /** Advances the animation, then turns the head if asked (after the mixer, which owns the bones). */
  update(dt: number): void {
    this.mixer.update(dt);
    if (this.head && this.lookWeight > 0) {
      const parent = this.head.parent;
      if (parent) {
        parent.updateWorldMatrix(true, false);
        tmpM.position.setFromMatrixPosition(this.head.matrixWorld);
        tmpM.quaternion.set(0, 0, 0, 1);
        tmpM.lookAt(this.lookTarget);
        // the look-at in the parent's space, blended with the animated rotation
        parent.getWorldQuaternion(tmpQ).invert();
        tmpQ.multiply(tmpM.quaternion);
        this.head.quaternion.slerp(tmpQ, this.lookWeight * 0.6);
      }
    }
  }

  dispose(): void {
    this.mixer.stopAllAction();
    for (const mesh of this.meshes()) {
      mesh.geometry.dispose();
      const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      for (const m of mats as Material[]) {
        for (const value of Object.values(m)) {
          if (value && typeof value === 'object' && 'isTexture' in value) (value as { dispose(): void }).dispose();
        }
        m.dispose();
      }
    }
    this.root.removeFromParent();
    tmpV.set(0, 0, 0);
  }
}

export { UP };
