/*
 * Palette check — proves src/theme/tokens.css is inside the sRGB gamut and meets
 * WCAG contrast at every one of the 360 base hues, including the side-lobe offsets
 * at their extremes and the chroma multipliers at both ends of their ranges.
 *
 * Zero dependencies. Run with `npx tsx scripts/palette-check.ts`.
 *   --fallback <hue>   print the hex ':root { ... }' block for that hue instead of checking
 *
 * Maths: OKLCH → OKLab → linear sRGB with Björn Ottosson's matrices; WCAG relative
 * luminance from linear RGB; alpha tokens are composited over --bg (in gamma-encoded
 * sRGB, as browsers blend) before measuring.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { LIGHT, MAX_HUE_OFFSET, warmEasing } from '../src/light/palette';

const TOKENS_PATH = resolve(process.cwd(), 'src/theme/tokens.css');

/** Tolerance on linear channels: anything inside [-GAMUT_TOL, 1 + GAMUT_TOL] counts as in gamut. */
const GAMUT_TOL = 0.0005;

/** Hue variable each token can reference, or a fixed hue in degrees. */
type HueRef = 'h' | 'h2' | 'h3' | number;

interface Token {
  name: string;
  /** OKLCH lightness, 0..1. */
  L: number;
  /** Base chroma before the --k / --kw / --kb multipliers. */
  C: number;
  hue: HueRef;
  /** Uses the warm-sector easing multiplier --kw. */
  kw: boolean;
  /** Uses the lobe-chroma breath multiplier --kb. */
  kb: boolean;
  /** Alpha, 1 when opaque. */
  alpha: number;
}

/** Linear sRGB triple. */
type Lin = readonly [number, number, number];

/* ----------------------------------------------------------------------------
 * Parsing
 * ------------------------------------------------------------------------- */

/**
 * Matches `--name: oklch(L <C> <H> [/ a])` where <C> is a number or
 * `calc(number * var(--k)[ * var(--kw)][ * var(--kb)])` and <H> is
 * var(--h) | var(--h2) | var(--h3) | number.
 */
const TOKEN_RE =
  /--([a-z0-9-]+)\s*:\s*oklch\(\s*([\d.]+)\s+(?:([\d.]+)|calc\(\s*([\d.]+)\s*\*\s*var\(--k\)((?:\s*\*\s*var\(--k[wb]\))*)\s*\))\s+(?:var\(--(h[23]?)\)|([\d.]+))\s*(?:\/\s*([\d.]+))?\s*\)/g;

/**
 * Reads every oklch token from the sheet. The first definition of a name wins:
 * later ones are the media-query overrides (prefers-contrast), which only ever
 * move ink darker and lines stronger.
 */
function parseTokens(css: string): Token[] {
  const seen = new Map<string, Token>();
  for (const m of css.matchAll(TOKEN_RE)) {
    const [, name, L, plainC, calcC, multipliers, hueVar, hueNum, alpha] = m;
    if (seen.has(name)) continue;
    seen.set(name, {
      name,
      L: Number(L),
      C: Number(plainC ?? calcC),
      hue: hueVar ? (hueVar as 'h' | 'h2' | 'h3') : Number(hueNum),
      kw: (multipliers ?? '').includes('--kw'),
      kb: (multipliers ?? '').includes('--kb'),
      alpha: alpha === undefined ? 1 : Number(alpha),
    });
  }
  return [...seen.values()];
}

/* ----------------------------------------------------------------------------
 * Colour maths
 * ------------------------------------------------------------------------- */

/** OKLCH → linear sRGB (Ottosson). Values may fall outside 0..1 when out of gamut. */
function oklchToLinear(L: number, C: number, hueDeg: number): Lin {
  const hr = (hueDeg * Math.PI) / 180;
  const a = C * Math.cos(hr);
  const b = C * Math.sin(hr);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ * l_ * l_;
  const m = m_ * m_ * m_;
  const s = s_ * s_ * s_;
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
}

function inGamut([r, g, b]: Lin): boolean {
  return [r, g, b].every((c) => c >= -GAMUT_TOL && c <= 1 + GAMUT_TOL);
}

const clamp01 = (x: number): number => Math.min(1, Math.max(0, x));

/** Linear → gamma-encoded sRGB, 0..1. */
function encode(c: number): number {
  const x = clamp01(c);
  return x <= 0.0031308 ? 12.92 * x : 1.055 * Math.pow(x, 1 / 2.4) - 0.055;
}

/** Gamma-encoded sRGB → linear. */
function decode(c: number): number {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

/** WCAG relative luminance from linear sRGB. */
function luminance([r, g, b]: Lin): number {
  return 0.2126 * clamp01(r) + 0.7152 * clamp01(g) + 0.0722 * clamp01(b);
}

/** Composites `fg` with `alpha` over opaque `bg`, blending in gamma space like a browser. */
function composite(fg: Lin, alpha: number, bg: Lin): Lin {
  if (alpha >= 1) return fg;
  const mix = (f: number, b: number) => decode(alpha * encode(f) + (1 - alpha) * encode(b));
  return [mix(fg[0], bg[0]), mix(fg[1], bg[1]), mix(fg[2], bg[2])];
}

function contrast(a: Lin, b: Lin): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/* ----------------------------------------------------------------------------
 * Evaluation
 * ------------------------------------------------------------------------- */

/** Lobe offsets at the extremes of their breath (nominal +35 ± 10 and −45 ± 10). */
const H2_OFFSETS = [25, 45];
const H3_OFFSETS = [-35, -55];
/** Warm-sector easing range. */
const KW_RANGE = [0.75, 1];
/** Lobe-chroma breath range. */
const KB_RANGE = [0.85, 1];

const mod360 = (x: number): number => ((x % 360) + 360) % 360;

/** Every (linear RGB, alpha) a token can take at a given base hue, with --k = 1. */
function variants(t: Token, baseHue: number): Lin[] {
  const hues =
    t.hue === 'h'
      ? [baseHue]
      : t.hue === 'h2'
        ? H2_OFFSETS.map((o) => mod360(baseHue + o))
        : t.hue === 'h3'
          ? H3_OFFSETS.map((o) => mod360(baseHue + o))
          : [t.hue];
  const kws = t.kw ? KW_RANGE : [1];
  const kbs = t.kb ? KB_RANGE : [1];
  const out: Lin[] = [];
  for (const h of hues) for (const kw of kws) for (const kb of kbs) out.push(oklchToLinear(t.L, t.C * kw * kb, h));
  return out;
}

/** A token's nominal colour (offsets +35 / −45, all multipliers 1), as used for the fallback. */
function nominal(t: Token, baseHue: number): Lin {
  const h = t.hue === 'h' ? baseHue : t.hue === 'h2' ? mod360(baseHue + 35) : t.hue === 'h3' ? mod360(baseHue - 45) : t.hue;
  return oklchToLinear(t.L, t.C, h);
}

interface Pair {
  fg: string;
  bg: string;
  min: number;
}

/** Required contrast pairs: foreground over background, minimum WCAG ratio. */
const PAIRS: Pair[] = [
  { fg: 'text', bg: 'bg', min: 4.5 },
  { fg: 'text', bg: 'surface', min: 4.5 },
  { fg: 'text', bg: 'surface-2', min: 4.5 },
  { fg: 'text', bg: 'lobe-1', min: 4.5 },
  { fg: 'text', bg: 'lobe-2', min: 4.5 },
  { fg: 'text', bg: 'lobe-3', min: 4.5 },
  { fg: 'text-muted', bg: 'bg', min: 4.5 },
  { fg: 'text-muted', bg: 'surface-2', min: 4.5 },
  // Radar numerals and labels (muted) sit over the ambient lobes.
  { fg: 'text-muted', bg: 'lobe-1', min: 4.5 },
  { fg: 'text-muted', bg: 'lobe-2', min: 4.5 },
  { fg: 'text-muted', bg: 'lobe-3', min: 4.5 },
  // --text-faint is for placeholders and disabled text only: it does not reach 4.5:1 anywhere
  // (worst about 3.6:1 over the lobes), so it is held at the 3:1 floor and must not carry data.
  { fg: 'text-faint', bg: 'bg', min: 3 },
  { fg: 'text-faint', bg: 'surface-2', min: 3 },
  { fg: 'text-faint', bg: 'lobe-1', min: 3 },
  { fg: 'text-faint', bg: 'lobe-2', min: 3 },
  { fg: 'text-faint', bg: 'lobe-3', min: 3 },
  { fg: 'accent', bg: 'bg', min: 4.5 },
  { fg: 'surface', bg: 'accent-strong', min: 4.5 },
  { fg: 'danger', bg: 'bg', min: 4.5 },
  { fg: 'accent-ui', bg: 'bg', min: 3 },
  { fg: 'accent-ui', bg: 'surface', min: 3 },
  { fg: 'accent-ui', bg: 'lobe-1', min: 3 },
  { fg: 'accent-ui', bg: 'lobe-2', min: 3 },
  { fg: 'accent-ui', bg: 'lobe-3', min: 3 },
  { fg: 'line-strong', bg: 'bg', min: 3 },
  // The portfolio's Old Newspaper skin (fixed hue): body ink and secondary ink on paper.
  { fg: 'press-ink', bg: 'press-paper', min: 7 },
  { fg: 'press-ink-2', bg: 'press-paper', min: 4.5 },
];

interface PairResult extends Pair {
  worst: number;
  worstHue: number;
}

interface GamutFailure {
  name: string;
  hue: number;
}

function check(tokens: Token[]): { pairs: PairResult[]; gamut: GamutFailure[]; missing: string[] } {
  const byName = new Map(tokens.map((t) => [t.name, t]));
  const missing = [...new Set(PAIRS.flatMap((p) => [p.fg, p.bg]))].filter((n) => !byName.has(n));
  const gamut: GamutFailure[] = [];
  const results: PairResult[] = PAIRS.filter((p) => byName.has(p.fg) && byName.has(p.bg)).map((p) => ({
    ...p,
    worst: Infinity,
    worstHue: -1,
  }));

  for (let hue = 0; hue < 360; hue++) {
    const colours = new Map<string, Lin[]>();
    for (const t of tokens) {
      const vs = variants(t, hue);
      if (!vs.every(inGamut)) gamut.push({ name: t.name, hue });
      colours.set(t.name, vs);
    }
    // Alpha tokens are measured as seen: composited over the field.
    const field = colours.get('bg') ?? [oklchToLinear(1, 0, 0)];
    const seen = (name: string): Lin[] => {
      const t = byName.get(name)!;
      const vs = colours.get(name)!;
      return t.alpha >= 1 ? vs : vs.flatMap((v) => field.map((f) => composite(v, t.alpha, f)));
    };
    for (const r of results) {
      for (const f of seen(r.fg)) {
        for (const b of seen(r.bg)) {
          const ratio = contrast(f, b);
          if (ratio < r.worst) {
            r.worst = ratio;
            r.worstHue = hue;
          }
        }
      }
    }
  }
  return { pairs: results, gamut, missing };
}

/* ----------------------------------------------------------------------------
 * The cursor light (src/light/palette.ts): its deepest colour under every text
 * role that sits on the bare field on Home. The shader reads the same numbers,
 * so what is proved here is what is drawn.
 * ------------------------------------------------------------------------- */

/**
 * Foreground roles on the bare field on Home, and the floor each keeps over the light's body.
 * --line-strong is not among them: on Home it only draws the drop zone's dashed border and the
 * field underline, both inside the opaque panel the light never shows through.
 */
const LIGHT_PAIRS: { fg: string; min: number }[] = [
  { fg: 'text', min: 4.5 },
  { fg: 'text-muted', min: 4.5 },
  // the italic headline line (40–56 px, large text) and the drop-zone icon: the 3:1 floor
  { fg: 'accent', min: 3 },
  { fg: 'accent-ui', min: 3 },
];

/** The body colours the light can take at a base hue: the wander window in 7° steps, then the halo offset. */
function lightBodies(baseHue: number): Lin[] {
  const out: Lin[] = [];
  for (let off = -MAX_HUE_OFFSET; off <= MAX_HUE_OFFSET; off += 7) {
    const heartHue = mod360(baseHue + off);
    out.push(oklchToLinear(LIGHT.bodyL, LIGHT.bodyC * warmEasing(heartHue), mod360(heartHue + LIGHT.haloHueOffset)));
  }
  return out;
}

function checkLight(tokens: Token[]): { pairs: PairResult[]; gamut: number[] } {
  const byName = new Map(tokens.map((t) => [t.name, t]));
  const results: PairResult[] = LIGHT_PAIRS.filter((p) => byName.has(p.fg)).map((p) => ({
    fg: p.fg,
    bg: 'light-body',
    min: p.min,
    worst: Infinity,
    worstHue: -1,
  }));
  const gamut: number[] = [];
  for (let hue = 0; hue < 360; hue++) {
    const bodies = lightBodies(hue);
    if (!bodies.every(inGamut)) gamut.push(hue);
    for (const r of results) {
      for (const f of variants(byName.get(r.fg)!, hue)) {
        for (const b of bodies) {
          const ratio = contrast(f, b);
          if (ratio < r.worst) {
            r.worst = ratio;
            r.worstHue = hue;
          }
        }
      }
    }
  }
  return { pairs: results, gamut };
}

/* ----------------------------------------------------------------------------
 * Fallback emission
 * ------------------------------------------------------------------------- */

function toHex(lin: Lin): string {
  return '#' + lin.map((c) => Math.round(encode(c) * 255).toString(16).padStart(2, '0')).join('');
}

function toRgba(lin: Lin, alpha: number): string {
  const [r, g, b] = lin.map((c) => Math.round(encode(c) * 255));
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** The ':root { ... }' hex block for one base hue at k = kw = kb = 1. */
function fallbackBlock(tokens: Token[], hue: number): string {
  const lines = tokens.map((t) => {
    const lin = nominal(t, hue);
    return `  --${t.name}: ${t.alpha >= 1 ? toHex(lin) : toRgba(lin, t.alpha)};`;
  });
  return `:root {\n${lines.join('\n')}\n}`;
}

/* ----------------------------------------------------------------------------
 * CLI
 * ------------------------------------------------------------------------- */

function main(): number {
  const css = readFileSync(TOKENS_PATH, 'utf8');
  const tokens = parseTokens(css);
  if (tokens.length === 0) {
    console.error(`palette-check: no oklch tokens found in ${TOKENS_PATH}`);
    return 1;
  }

  const args = process.argv.slice(2);
  const fallbackAt = args.indexOf('--fallback');
  if (fallbackAt !== -1) {
    const hue = Number(args[fallbackAt + 1]);
    if (!Number.isInteger(hue) || hue < 0 || hue > 359) {
      console.error('palette-check: --fallback needs an integer hue 0..359');
      return 1;
    }
    console.log(fallbackBlock(tokens, hue));
    return 0;
  }

  const { pairs, gamut, missing } = check(tokens);
  let failed = false;

  console.log(`palette-check: ${tokens.length} tokens, 360 hues\n`);
  const header = ['pair', 'worst', 'at hue', 'min', 'result'];
  const rows = pairs.map((r) => {
    const ok = r.worst >= r.min;
    failed ||= !ok;
    return [`${r.fg} / ${r.bg}`, r.worst.toFixed(2), String(r.worstHue), r.min.toFixed(1), ok ? 'pass' : 'FAIL'];
  });
  const widths = header.map((h, i) => Math.max(h.length, ...rows.map((row) => row[i].length)));
  const fmt = (row: string[]) => row.map((c, i) => c.padEnd(widths[i])).join('  ');
  console.log(fmt(header));
  console.log(widths.map((w) => '-'.repeat(w)).join('  '));
  for (const row of rows) console.log(fmt(row));

  if (missing.length) {
    failed = true;
    console.log(`\nMissing tokens: ${missing.join(', ')}`);
  }

  if (gamut.length) {
    failed = true;
    const byToken = new Map<string, number[]>();
    for (const g of gamut) byToken.set(g.name, [...(byToken.get(g.name) ?? []), g.hue]);
    console.log('\nOut of sRGB gamut:');
    for (const [name, hues] of byToken) {
      console.log(`  --${name}: ${hues.length} hue(s), e.g. ${hues.slice(0, 6).join(', ')}${hues.length > 6 ? ', …' : ''}`);
    }
  } else {
    console.log('\nGamut: every token inside sRGB at every hue.');
  }

  const light = checkLight(tokens);
  console.log(`\ncursor light: body L ${LIGHT.bodyL}, C ${LIGHT.bodyC}, hue within ±${MAX_HUE_OFFSET}° of the field (+${LIGHT.haloHueOffset}° halo)\n`);
  const lightRows = light.pairs.map((r) => {
    const ok = r.worst >= r.min;
    failed ||= !ok;
    return [`${r.fg} / ${r.bg}`, r.worst.toFixed(2), String(r.worstHue), r.min.toFixed(1), ok ? 'pass' : 'FAIL'];
  });
  const lightWidths = header.map((h, i) => Math.max(h.length, ...lightRows.map((row) => row[i].length)));
  const fmtLight = (row: string[]) => row.map((c, i) => c.padEnd(lightWidths[i])).join('  ');
  console.log(fmtLight(header));
  console.log(lightWidths.map((w) => '-'.repeat(w)).join('  '));
  for (const row of lightRows) console.log(fmtLight(row));
  if (light.gamut.length) {
    failed = true;
    console.log(`\nLight body out of sRGB gamut at ${light.gamut.length} hue(s), e.g. ${light.gamut.slice(0, 6).join(', ')}`);
  } else {
    console.log('\nLight body inside sRGB at every hue.');
  }

  console.log(failed ? '\nFAILED' : '\nOK');
  return failed ? 1 : 0;
}

process.exit(main());
