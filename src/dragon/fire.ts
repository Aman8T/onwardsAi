/*
 * Fire, embers, smoke and shell: three.quarks particle systems in the dragon's scene. The breath is a
 * cone from the mouth aimed at the target, white → yellow → orange → red → gone, growing as it travels and
 * drifting up; embers burst where the flame lands; smoke rises from the scorch; the shell flies off the
 * egg when it hatches. Sprites are Kenney's CC0 particle pack. World units are CSS px.
 */
import { AdditiveBlending, MeshBasicMaterial, NormalBlending, PointLight, SRGBColorSpace, TextureLoader, Vector3 } from 'three';
import type { Scene } from 'three';
import {
  ApplyForce,
  BatchedRenderer,
  Bezier,
  ColorOverLife,
  ConeEmitter,
  ConstantValue,
  Gradient,
  IntervalValue,
  ParticleSystem,
  PiecewiseBezier,
  RandomColor,
  RenderMode,
  SizeOverLife,
  SpeedOverLife,
  SphereEmitter,
  Vector3 as QVector3,
  Vector4 as QVector4,
} from 'three.quarks';

const FX = '/dragons/fx/';

const loader = new TextureLoader();

function sprite(name: string, additive: boolean): MeshBasicMaterial {
  const map = loader.load(`${FX}${name}.png`);
  map.colorSpace = SRGBColorSpace;
  return new MeshBasicMaterial({ map, blending: additive ? AdditiveBlending : NormalBlending, transparent: true, depthWrite: false });
}

const curve = (a: number, b: number, c: number, d: number) => new PiecewiseBezier([[new Bezier(a, b, c, d), 0]]);

export class Fire {
  private readonly batch = new BatchedRenderer();
  private readonly breath: ParticleSystem;
  private readonly embers: ParticleSystem;
  private readonly smoke: ParticleSystem;
  private readonly shell: ParticleSystem;
  private readonly glow: PointLight;
  private breathUntil = 0;
  private breathRate = 0;
  private emberUntil = 0;
  private smokeUntil = 0;
  private shellUntil = 0;
  private readonly aim = new Vector3();

  constructor(private readonly scene: Scene) {
    scene.add(this.batch);

    this.breath = new ParticleSystem({
      duration: 1,
      looping: true,
      worldSpace: true,
      startLife: new IntervalValue(0.45, 0.75),
      startSpeed: new IntervalValue(420, 640),
      startSize: new IntervalValue(26, 44),
      startColor: new RandomColor(new QVector4(1, 0.98, 0.85, 1), new QVector4(1, 0.9, 0.6, 1)),
      emissionOverTime: new ConstantValue(0),
      shape: new ConeEmitter({ radius: 6, angle: Math.PI / 11 }),
      material: sprite('flame_02', true),
      renderMode: RenderMode.BillBoard,
      renderOrder: 3,
    });
    this.breath.addBehavior(
      new ColorOverLife(
        new Gradient(
          [
            [new QVector3(1, 1, 0.9), 0],
            [new QVector3(1, 0.78, 0.3), 0.22],
            [new QVector3(1, 0.42, 0.1), 0.55],
            [new QVector3(0.55, 0.12, 0.04), 1],
          ],
          [
            [1, 0],
            [1, 0.35],
            [0.55, 0.7],
            [0, 1],
          ],
        ),
      ),
    );
    this.breath.addBehavior(new SizeOverLife(curve(0.5, 1.3, 2.1, 2.8)));
    this.breath.addBehavior(new SpeedOverLife(curve(1, 0.6, 0.35, 0.2)));
    this.breath.addBehavior(new ApplyForce(new QVector3(0, 1, 0), new ConstantValue(110)));

    this.embers = new ParticleSystem({
      duration: 1,
      looping: true,
      worldSpace: true,
      startLife: new IntervalValue(0.6, 1.3),
      startSpeed: new IntervalValue(120, 320),
      startSize: new IntervalValue(5, 11),
      startColor: new RandomColor(new QVector4(1, 0.8, 0.35, 1), new QVector4(1, 0.5, 0.15, 1)),
      emissionOverTime: new ConstantValue(0),
      shape: new SphereEmitter({ radius: 10 }),
      material: sprite('spark_02', true),
      renderMode: RenderMode.BillBoard,
      renderOrder: 3,
    });
    this.embers.addBehavior(new ApplyForce(new QVector3(0, -1, 0), new ConstantValue(380)));
    this.embers.addBehavior(new SizeOverLife(curve(1, 0.9, 0.5, 0)));

    this.smoke = new ParticleSystem({
      duration: 1,
      looping: true,
      worldSpace: true,
      startLife: new IntervalValue(1.6, 2.6),
      startSpeed: new IntervalValue(30, 70),
      startSize: new IntervalValue(18, 34),
      startColor: new RandomColor(new QVector4(0.42, 0.4, 0.38, 0.28), new QVector4(0.6, 0.58, 0.55, 0.2)),
      emissionOverTime: new ConstantValue(0),
      shape: new ConeEmitter({ radius: 18, angle: Math.PI / 5 }),
      material: sprite('smoke_03', false),
      renderMode: RenderMode.BillBoard,
      renderOrder: 2,
    });
    this.smoke.addBehavior(new SizeOverLife(curve(0.6, 1.4, 2.2, 3)));
    this.smoke.addBehavior(
      new ColorOverLife(
        new Gradient(
          [
            [new QVector3(0.5, 0.48, 0.46), 0],
            [new QVector3(0.7, 0.68, 0.66), 1],
          ],
          [
            [0.26, 0],
            [0.18, 0.4],
            [0, 1],
          ],
        ),
      ),
    );
    this.smoke.addBehavior(new ApplyForce(new QVector3(0, 1, 0), new ConstantValue(40)));
    // smoke rises: the cone points up
    this.smoke.emitter.rotation.x = -Math.PI / 2;

    this.shell = new ParticleSystem({
      duration: 1,
      looping: true,
      worldSpace: true,
      startLife: new IntervalValue(0.7, 1.2),
      startSpeed: new IntervalValue(160, 360),
      startSize: new IntervalValue(6, 14),
      startColor: new RandomColor(new QVector4(0.95, 0.9, 0.8, 1), new QVector4(0.8, 0.72, 0.6, 1)),
      emissionOverTime: new ConstantValue(0),
      shape: new SphereEmitter({ radius: 12 }),
      material: sprite('scorch_02', false),
      renderMode: RenderMode.BillBoard,
      renderOrder: 3,
    });
    this.shell.addBehavior(new ApplyForce(new QVector3(0, -1, 0), new ConstantValue(520)));
    this.shell.addBehavior(new SizeOverLife(curve(1, 1, 0.8, 0)));

    for (const ps of [this.breath, this.embers, this.smoke, this.shell]) {
      this.batch.addSystem(ps);
      scene.add(ps.emitter);
    }

    this.glow = new PointLight(0xff9a40, 0, 900, 2);
    scene.add(this.glow);
  }

  /** Starts a breath from `origin` towards `target` for `seconds`; `strength` scales rate and size. */
  breathe(origin: Vector3, target: Vector3, seconds: number, strength: number, now: number): void {
    this.breathUntil = now + seconds;
    this.breathRate = 160 + 220 * strength;
    this.breath.startSize = new IntervalValue(18 + 14 * strength, 30 + 26 * strength);
    this.breath.startSpeed = new IntervalValue(360 + 180 * strength, 520 + 260 * strength);
    this.breath.emissionOverTime = new ConstantValue(this.breathRate);
    this.follow(origin, target);
  }

  /** Keeps the breath on the mouth and aimed while the dragon hovers. */
  follow(origin: Vector3, target: Vector3): void {
    this.breath.emitter.position.copy(origin);
    this.aim.copy(target);
    this.breath.emitter.lookAt(this.aim);
    this.glow.position.copy(origin);
  }

  /** A short puff (a roar, a hatchling's first flame) from `origin` along `dir`. */
  puff(origin: Vector3, dir: Vector3, strength: number, now: number): void {
    this.aim.copy(origin).addScaledVector(dir, 200);
    this.breathe(origin, this.aim, 0.35, strength, now);
  }

  stopBreath(): void {
    this.breathUntil = 0;
    this.breath.emissionOverTime = new ConstantValue(0);
  }

  /** Embers and smoke where the flame lands (a page point at z = 0, slightly raised). */
  ignite(at: Vector3, strength: number, now: number): void {
    this.embers.emitter.position.copy(at);
    this.embers.emissionOverTime = new ConstantValue(240 + 300 * strength);
    this.emberUntil = now + 0.3;
    this.smoke.emitter.position.copy(at);
    this.smoke.emissionOverTime = new ConstantValue(14 + 10 * strength);
    this.smokeUntil = now + 3;
  }

  /** The egg's shell flying apart. */
  shells(at: Vector3, now: number): void {
    this.shell.emitter.position.copy(at);
    this.shell.emissionOverTime = new ConstantValue(260);
    this.shellUntil = now + 0.25;
  }

  update(dt: number, now: number): void {
    if (this.breathUntil && now >= this.breathUntil) this.stopBreath();
    if (this.emberUntil && now >= this.emberUntil) {
      this.embers.emissionOverTime = new ConstantValue(0);
      this.emberUntil = 0;
    }
    if (this.smokeUntil && now >= this.smokeUntil) {
      this.smoke.emissionOverTime = new ConstantValue(0);
      this.smokeUntil = 0;
    }
    if (this.shellUntil && now >= this.shellUntil) {
      this.shell.emissionOverTime = new ConstantValue(0);
      this.shellUntil = 0;
    }
    // the mouth's glow flickers while the breath is on
    const breathing = this.breathUntil > now;
    const target = breathing ? 36000 * (0.8 + 0.4 * Math.random()) : 0;
    this.glow.intensity += (target - this.glow.intensity) * (1 - Math.exp(-dt / 0.06));
    this.batch.update(dt);
  }

  /** Live particles, for the dev read-out. */
  count(): number {
    return this.breath.particleNum + this.embers.particleNum + this.smoke.particleNum + this.shell.particleNum;
  }

  dispose(): void {
    for (const ps of [this.breath, this.embers, this.smoke, this.shell]) {
      this.batch.deleteSystem(ps);
      ps.emitter.removeFromParent();
      const m = ps.material as MeshBasicMaterial;
      m.map?.dispose();
      m.dispose();
      ps.dispose();
    }
    this.glow.removeFromParent();
    this.batch.removeFromParent();
    this.scene.remove(this.batch);
  }
}
