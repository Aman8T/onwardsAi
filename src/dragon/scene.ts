/*
 * The dragon's stage: one transparent WebGL canvas over the whole page, a camera placed so that the plane
 * z = 0 maps one world unit to one CSS pixel (x right, y up, origin at the viewport's centre), and lights
 * tinted from the page's own colour tokens so the creature sits in the same atmosphere as everything else.
 * Depth is a small range around the page: z < 0 is farther and smaller, z > 0 nearer and larger.
 */
import {
  ACESFilmicToneMapping,
  Color,
  DirectionalLight,
  HemisphereLight,
  PerspectiveCamera,
  PMREMGenerator,
  Scene,
  SRGBColorSpace,
  Vector3,
  WebGLRenderer,
} from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const FOV = 35;
const MAX_DPR = 1.5;

export interface Bounds {
  /** Half extents in px (world units) from the origin at the viewport's centre. */
  hx: number;
  hy: number;
  zMin: number;
  zMax: number;
}

/** Reads any CSS colour (oklch included) into a three.js Color through a 1×1 canvas. */
class ColourProbe {
  private readonly probe: HTMLSpanElement;
  private readonly ctx: CanvasRenderingContext2D | null;

  constructor(host: HTMLElement) {
    this.probe = document.createElement('span');
    this.probe.style.position = 'absolute';
    this.probe.style.width = '0';
    this.probe.style.height = '0';
    this.probe.style.overflow = 'hidden';
    host.appendChild(this.probe);
    const c = document.createElement('canvas');
    c.width = 1;
    c.height = 1;
    this.ctx = c.getContext('2d', { willReadFrequently: true });
  }

  read(cssVar: string, into: Color): void {
    if (!this.ctx) return;
    this.probe.style.color = `var(${cssVar})`;
    const value = getComputedStyle(this.probe).color;
    this.ctx.fillStyle = value;
    this.ctx.fillRect(0, 0, 1, 1);
    const [r, g, b] = this.ctx.getImageData(0, 0, 1, 1).data;
    into.setRGB(r / 255, g / 255, b / 255, SRGBColorSpace);
  }

  dispose(): void {
    this.probe.remove();
  }
}

export class DragonScene {
  readonly canvas: HTMLCanvasElement;
  readonly renderer: WebGLRenderer;
  readonly scene = new Scene();
  readonly camera: PerspectiveCamera;
  width = 1;
  height = 1;
  /** Camera distance from the z = 0 plane, px. */
  distance = 1;
  private readonly hemi: HemisphereLight;
  private readonly key: DirectionalLight;
  private readonly probe: ColourProbe;
  private readonly pmrem: PMREMGenerator;
  private tintedAt = -1e9;
  private readonly tmp = new Vector3();

  constructor(host: HTMLElement) {
    this.canvas = document.createElement('canvas');
    this.canvas.setAttribute('aria-hidden', 'true');
    host.appendChild(this.canvas);
    this.renderer = new WebGLRenderer({
      canvas: this.canvas,
      alpha: true,
      antialias: true,
      premultipliedAlpha: true,
      powerPreference: 'high-performance',
      preserveDrawingBuffer: false,
    });
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.outputColorSpace = SRGBColorSpace;
    this.renderer.toneMapping = ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    this.camera = new PerspectiveCamera(FOV, 1, 10, 6000);
    this.hemi = new HemisphereLight(0xffffff, 0x888888, 1.4);
    this.key = new DirectionalLight(0xffffff, 2.2);
    this.scene.add(this.hemi, this.key);
    this.pmrem = new PMREMGenerator(this.renderer);
    this.scene.environment = this.pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.environmentIntensity = 0.55;
    this.probe = new ColourProbe(host);
    this.resize();
  }

  /** Fits the canvas and the camera to the viewport: one world unit = one CSS px on the page plane. */
  resize(): void {
    this.width = Math.max(1, window.innerWidth);
    this.height = Math.max(1, window.innerHeight);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, MAX_DPR));
    this.renderer.setSize(this.width, this.height, false);
    this.distance = this.height / 2 / Math.tan((FOV / 2) * (Math.PI / 180));
    this.camera.aspect = this.width / this.height;
    this.camera.position.set(0, 0, this.distance);
    this.camera.lookAt(0, 0, 0);
    this.camera.updateProjectionMatrix();
    this.key.position.set(-this.width * 0.4, this.height * 0.6, this.distance * 0.8);
    this.key.target.position.set(0, 0, 0);
  }

  bounds(margin = 56): Bounds {
    return { hx: this.width / 2 - margin, hy: this.height / 2 - margin, zMin: -420, zMax: 140 };
  }

  /** A screen point (CSS px, y down) on the page plane, or at depth `z`. */
  toWorld(x: number, y: number, z = 0, into = new Vector3()): Vector3 {
    // perspective: a point at depth z that sits over the same screen pixel is scaled by (distance - z) / distance
    const k = (this.distance - z) / this.distance;
    return into.set((x - this.width / 2) * k, (this.height / 2 - y) * k, z);
  }

  /** A world point projected to CSS px (y down). */
  toScreen(v: Vector3, into: { x: number; y: number } = { x: 0, y: 0 }): { x: number; y: number } {
    this.tmp.copy(v).project(this.camera);
    into.x = ((this.tmp.x + 1) / 2) * this.width;
    into.y = ((1 - this.tmp.y) / 2) * this.height;
    return into;
  }

  /** Re-tints the lights from the page's tokens (every 7 s: the hue steps that often). */
  tint(now: number): void {
    if (now - this.tintedAt < 7) return;
    this.tintedAt = now;
    this.probe.read('--lobe-1', this.hemi.color);
    this.probe.read('--surface-2', this.hemi.groundColor);
    this.probe.read('--surface', this.key.color);
  }

  render(): void {
    this.renderer.render(this.scene, this.camera);
  }

  dispose(): void {
    this.probe.dispose();
    this.scene.environment?.dispose();
    this.pmrem.dispose();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.canvas.remove();
  }
}
