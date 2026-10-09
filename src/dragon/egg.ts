/*
 * The egg: a speckled ellipsoid on the panel's edge for the first twenty seconds. It rocks more and more,
 * cracks appear at seventeen seconds, and at twenty the engine swaps it for the hatchling and a burst of
 * shell. Procedural: a sphere scaled into an egg, a 128 px canvas for the shell's speckles and cracks.
 */
import { CanvasTexture, Mesh, MeshStandardMaterial, SphereGeometry, SRGBColorSpace, Vector3 } from 'three';

const CRACK_AGE = 17;
const HATCH_AGE = 20;

function rand(seed: number): () => number {
  let s = seed >>> 0 || 1;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

export class Egg {
  readonly mesh: Mesh;
  private readonly canvas: HTMLCanvasElement;
  private readonly texture: CanvasTexture;
  private cracked = false;
  /** Height in px. */
  readonly height: number;

  constructor(height = 48) {
    this.height = height;
    this.canvas = document.createElement('canvas');
    this.canvas.width = 256;
    this.canvas.height = 256;
    this.paintShell();
    this.texture = new CanvasTexture(this.canvas);
    this.texture.colorSpace = SRGBColorSpace;
    const geometry = new SphereGeometry(1, 28, 20);
    geometry.scale((height / 2) * 0.74, height / 2, (height / 2) * 0.74);
    const material = new MeshStandardMaterial({ map: this.texture, roughness: 0.55, metalness: 0 });
    this.mesh = new Mesh(geometry, material);
    this.mesh.frustumCulled = false;
  }

  private paintShell(): void {
    const ctx = this.canvas.getContext('2d');
    if (!ctx) return;
    const rnd = rand(11);
    ctx.fillStyle = 'oklch(0.93 0.03 80)';
    ctx.fillRect(0, 0, 256, 256);
    for (let i = 0; i < 260; i++) {
      const r = 1 + rnd() * 3.5;
      ctx.fillStyle = `oklch(${0.6 + rnd() * 0.2} 0.05 ${60 + rnd() * 30} / ${0.35 + rnd() * 0.4})`;
      ctx.beginPath();
      ctx.arc(rnd() * 256, rnd() * 256, r, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  private paintCracks(): void {
    const ctx = this.canvas.getContext('2d');
    if (!ctx) return;
    const rnd = rand(29);
    ctx.strokeStyle = 'oklch(0.3 0.03 60 / 0.85)';
    ctx.lineWidth = 2.2;
    ctx.lineCap = 'round';
    for (let c = 0; c < 4; c++) {
      let x = 40 + rnd() * 176;
      let y = 90 + rnd() * 80;
      ctx.beginPath();
      ctx.moveTo(x, y);
      for (let i = 0; i < 7; i++) {
        x += (rnd() - 0.5) * 34;
        y += (rnd() - 0.3) * 26;
        ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
    this.texture.needsUpdate = true;
  }

  /** Places the egg standing on `at` (its bottom), rocking as the hatch nears. */
  update(age: number, now: number, at: Vector3): void {
    this.mesh.position.set(at.x, at.y + this.height / 2, at.z);
    const nearing = Math.min(1, Math.max(0, (age - 6) / (HATCH_AGE - 6)));
    const amp = nearing * nearing * 0.22;
    const wobble = Math.sin(now * 6.5) * Math.max(0, Math.sin(now * 0.9)) ;
    this.mesh.rotation.z = wobble * amp;
    this.mesh.rotation.x = Math.sin(now * 4.1) * amp * 0.4;
    if (!this.cracked && age >= CRACK_AGE) {
      this.cracked = true;
      this.paintCracks();
    }
  }

  dispose(): void {
    this.mesh.geometry.dispose();
    (this.mesh.material as MeshStandardMaterial).dispose();
    this.texture.dispose();
    this.mesh.removeFromParent();
  }
}
