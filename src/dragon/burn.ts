/*
 * The scorch: a DOM overlay over the target's box with a charred mask on a small canvas, and a gentle
 * SVG filter on the element itself for ten seconds. Soot never covers more than about 60 % of the box and
 * never exceeds 0.72 alpha, so dark text on charred paper still reads; overlays take no pointer events, so
 * a scorched button still clicks and a scorched field still types.
 *
 * Colours are written as oklch() strings for the canvas (tokens.css stays the only file with colour
 * syntax in stylesheets; the dragon's fire is its own thing and documented as such).
 */
import type { Target } from './targets';

export const BURN_SECONDS = 10;
const HEAL_SECONDS = 1.5;
const MAX_BURNS = 3;

const soot = (a: number) => `oklch(0.26 0.02 60 / ${a.toFixed(3)})`;
const ember = (a: number, l = 0.66) => `oklch(${l} 0.19 48 / ${a.toFixed(3)})`;
const ash = (a: number) => `oklch(0.8 0.01 80 / ${a.toFixed(3)})`;

interface Blob {
  x: number;
  y: number;
  r: number;
  a: number;
}

interface Burn {
  target: Target;
  host: HTMLDivElement;
  canvas: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
  start: number;
  bleed: number;
  strength: number;
  hit: { x: number; y: number };
  soot: Blob[];
  rim: Blob[];
  ash: Blob[];
  lastDraw: number;
  healing: boolean;
}

/** One SVG filter shared by every scorched element: desaturate, darken a little, and curl the paper. */
function ensureFilter(): void {
  if (document.getElementById('dragon-char')) return;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', '0');
  svg.setAttribute('height', '0');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('dragon-defs');
  svg.innerHTML =
    '<filter id="dragon-char" x="-10%" y="-10%" width="120%" height="120%" color-interpolation-filters="sRGB">' +
    '<feColorMatrix type="saturate" values="0.45" result="desat"/>' +
    '<feComponentTransfer in="desat" result="dark"><feFuncR type="linear" slope="0.78"/><feFuncG type="linear" slope="0.74"/><feFuncB type="linear" slope="0.68"/></feComponentTransfer>' +
    '<feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="2" seed="7" result="noise"/>' +
    '<feDisplacementMap in="dark" in2="noise" scale="2.5" xChannelSelector="R" yChannelSelector="G"/>' +
    '</filter>';
  document.body.appendChild(svg);
}

function rand(seed: number): () => number {
  let s = seed >>> 0 || 1;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

export class BurnLayer {
  private readonly layer: HTMLDivElement;
  private burns: Burn[] = [];

  constructor() {
    ensureFilter();
    this.layer = document.createElement('div');
    this.layer.className = 'burn-layer';
    this.layer.setAttribute('aria-hidden', 'true');
    document.body.appendChild(this.layer);
  }

  count(): number {
    return this.burns.filter((b) => !b.healing).length;
  }

  isBurning(key: string): boolean {
    return this.burns.some((b) => b.target.key === key && !b.healing);
  }

  /**
   * Scorches a target. `hit` is where the flame lands (CSS px); `strength` 0.3..1.4 scales the spread.
   * Returns false when the layer is full.
   */
  ignite(target: Target, hit: { x: number; y: number }, strength: number, bleed: number, now: number): boolean {
    if (this.count() >= MAX_BURNS || this.isBurning(target.key)) return false;
    const rect = target.el.getBoundingClientRect();
    const host = document.createElement('div');
    host.className = 'burn';
    const canvas = document.createElement('canvas');
    const w = Math.ceil(rect.width + bleed * 2);
    const h = Math.ceil(rect.height + bleed * 2);
    canvas.width = w;
    canvas.height = h;
    host.style.left = `${rect.left - bleed}px`;
    host.style.top = `${rect.top - bleed}px`;
    host.style.width = `${w}px`;
    host.style.height = `${h}px`;
    host.appendChild(canvas);
    this.layer.appendChild(host);
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      host.remove();
      return false;
    }
    target.el.classList.add('dragon-charred');

    // blobs cluster around where the flame hit, inside the box; the spread grows with strength
    const rnd = rand(Math.floor(now * 1000) ^ (w * 31 + h));
    const hx = Math.min(w - bleed, Math.max(bleed, hit.x - rect.left + bleed));
    const hy = Math.min(h - bleed, Math.max(bleed, hit.y - rect.top + bleed));
    const spreadX = (w * 0.32 + bleed) * (0.7 + 0.5 * strength);
    const spreadY = (h * 0.45 + bleed) * (0.7 + 0.5 * strength);
    const sootBlobs: Blob[] = [];
    const n = 26 + Math.round(18 * strength);
    for (let i = 0; i < n; i++) {
      const ang = rnd() * Math.PI * 2;
      const dist = Math.sqrt(rnd());
      sootBlobs.push({
        x: hx + Math.cos(ang) * dist * spreadX,
        y: hy + Math.sin(ang) * dist * spreadY,
        r: 10 + rnd() * (22 + 18 * strength),
        a: 0.14 + rnd() * 0.16,
      });
    }
    const rim: Blob[] = [];
    for (let i = 0; i < 18; i++) {
      const ang = rnd() * Math.PI * 2;
      const dist = 0.75 + rnd() * 0.4;
      rim.push({ x: hx + Math.cos(ang) * dist * spreadX, y: hy + Math.sin(ang) * dist * spreadY, r: 8 + rnd() * 14, a: 0.35 + rnd() * 0.35 });
    }
    const ashFlecks: Blob[] = [];
    for (let i = 0; i < 14; i++) {
      const ang = rnd() * Math.PI * 2;
      const dist = rnd() * 0.9;
      ashFlecks.push({ x: hx + Math.cos(ang) * dist * spreadX, y: hy + Math.sin(ang) * dist * spreadY, r: 1 + rnd() * 2.2, a: 0.5 + rnd() * 0.4 });
    }
    const burn: Burn = { target, host, canvas, ctx, start: now, bleed, strength, hit: { x: hx, y: hy }, soot: sootBlobs, rim, ash: ashFlecks, lastDraw: -1, healing: false };
    this.burns.push(burn);
    this.draw(burn, 0);
    return true;
  }

  /** Advances every scorch: redraws while it changes, keeps it over its element, heals it on time. */
  update(now: number): void {
    for (const b of this.burns) {
      const t = now - b.start;
      if (!b.healing && t >= BURN_SECONDS - HEAL_SECONDS) {
        b.healing = true;
        b.host.classList.add('is-healing');
        b.target.el.classList.remove('dragon-charred');
      }
      if (t >= BURN_SECONDS + 0.2) {
        b.host.remove();
        continue;
      }
      // follow the element (scroll, resize, layout) and redraw while the rim is still cooling
      const rect = b.target.el.getBoundingClientRect();
      b.host.style.left = `${rect.left - b.bleed}px`;
      b.host.style.top = `${rect.top - b.bleed}px`;
      if (t < 3 && now - b.lastDraw > 0.07) this.draw(b, t);
    }
    this.burns = this.burns.filter((b) => now - b.start < BURN_SECONDS + 0.2);
  }

  private draw(b: Burn, t: number): void {
    const { ctx, canvas } = b;
    b.lastDraw = t + b.start;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    // how far the burn has spread: fast in the first 0.6 s
    const spread = Math.min(1, t / 0.6);
    ctx.globalCompositeOperation = 'source-over';
    for (const s of b.soot) {
      const r = s.r * (0.4 + 0.6 * spread);
      const g = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, r);
      g.addColorStop(0, soot(s.a * spread));
      g.addColorStop(0.55, soot(s.a * 0.6 * spread));
      g.addColorStop(1, soot(0));
      ctx.fillStyle = g;
      ctx.fillRect(s.x - r, s.y - r, r * 2, r * 2);
    }
    // the ember rim glows for about two seconds, then cools to dark
    const glow = t < 0.3 ? t / 0.3 : Math.max(0, 1 - (t - 0.3) / 2);
    const lightness = 0.72 - 0.4 * (1 - glow);
    for (const e of b.rim) {
      const r = e.r * (0.6 + 0.4 * spread);
      const a = e.a * (0.25 + 0.75 * glow) * spread;
      const g = ctx.createRadialGradient(e.x, e.y, 0, e.x, e.y, r);
      g.addColorStop(0, ember(a, lightness));
      g.addColorStop(1, ember(0, lightness));
      ctx.fillStyle = g;
      ctx.fillRect(e.x - r, e.y - r, r * 2, r * 2);
    }
    // ash flecks settle as the glow dies
    const settle = Math.min(1, Math.max(0, (t - 0.8) / 1.5));
    for (const f of b.ash) {
      ctx.fillStyle = ash(f.a * settle);
      ctx.beginPath();
      ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  dispose(): void {
    for (const b of this.burns) {
      b.target.el.classList.remove('dragon-charred');
      b.host.remove();
    }
    this.burns = [];
    this.layer.remove();
    document.querySelector('svg.dragon-defs')?.remove();
    for (const el of document.querySelectorAll('.dragon-charred')) el.classList.remove('dragon-charred');
  }
}
