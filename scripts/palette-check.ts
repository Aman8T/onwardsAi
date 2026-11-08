/*
 * Palette check — proves src/theme/tokens.css is inside the sRGB gamut and meets
 * WCAG contrast at every one of the 360 base hues, including the side-lobe offsets
 * at their extremes and the chroma multipliers at both ends of their ranges.
 * Both schemes: the light table, and the dark table under `prefers-color-scheme: dark`
 * / `[data-scheme='dark']` (whose two copies must agree, and which must redefine
 * every role the light table defines). Alpha tokens are composited over each
 * scheme's own --bg. The cursor light's dye (src/light/palette.ts) is checked in
 * both schemes, at its peak alpha over the field and every lobe.
 *
 * Zero dependencies. Run with `npx tsx scripts/palette-check.ts`.
 *   --fallback <hue>   print the hex blocks for that hue (the light ':root', then the
 *                      dark media and attribute copies) instead of checking
 *
 * Maths: OKLCH → OKLab → linear sRGB with Björn Ottosson's matrices; WCAG relative
 * luminance from linear RGB; alpha tokens are composited over --bg (in gamma-encoded
 * sRGB, as browsers blend) before measuring.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { LIGHT, MAX_WANDER, warmEasing, wakePalette } from '../src/light/palette';

const TOKENS_PATH = resolve(process.cwd(), 'src/theme/tokens.css');

/** Tolerance on linear channels: anything inside [-GAMUT_TOL, 1 + GAMUT_TOL] counts as in gamut. */
const GAMUT_TOL = 0.0005;

/** Hue variable each token can reference, or a fixed hue in degrees. */
type HueRef = 'h' | 'h2' | 'h3' | 'h4' | number;

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
  /--([a-z0-9-]+)\s*:\s*oklch\(\s*([\d.]+)\s+(?:([\d.]+)|calc\(\s*([\d.]+)\s*\*\s*var\(--k\)((?:\s*\*\s*var\(--k[wb]\))*)\s*\))\s+(?:var\(--(h[234]?)\)|([\d.]+))\s*(?:\/\s*([\d.]+))?\s*\)/g;

/**
 * Reads every oklch token from a stretch of the sheet. The first definition of a
 * name wins: in the whole sheet that is the light table (the dark table and the
 * prefers-contrast overrides come after it); inside one block it is that block's own.
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
      hue: hueVar ? (hueVar as 'h' | 'h2' | 'h3' | 'h4') : Number(hueNum),
      kw: (multipliers ?? '').includes('--kw'),
      kb: (multipliers ?? '').includes('--kb'),
      alpha: alpha === undefined ? 1 : Number(alpha),
    });
  }
  return [...seen.values()];
}

/** Index of the `}` matching the `{` at `open`, or -1. */
function closingBrace(text: string, open: number): number {
  let depth = 0;
  for (let i = open; i < text.length; i++) {
    if (text[i] === '{') depth++;
    else if (text[i] === '}' && --depth === 0) return i;
  }
  return -1;
}

/**
 * The tokens of the first block opened by `opener` that defines any: that scheme copy's base
 * table. Earlier matches are the hex fallback (no oklch in it); later ones are the
 * prefers-contrast overrides, which come after the base table in the sheet.
 */
function firstTokenBlock(css: string, opener: RegExp): Token[] {
  for (const m of css.matchAll(opener)) {
    const open = m.index + m[0].length - 1;
    const close = closingBrace(css, open);
    const tokens = parseTokens(css.slice(open, close === -1 ? undefined : close));
    if (tokens.length) return tokens;
  }
  return [];
}

/** The dark table as the system preference applies it: inside `@media (prefers-color-scheme: dark)`. */
const DARK_MEDIA_RE = /@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)\s*\{/g;
/** The dark table as forced by <html data-scheme="dark">. */
const DARK_FORCED_RE = /:root:where\(\[data-scheme=['"]dark['"]\]\)(?:\s*,\s*\.look-today\.scheme-dark)?\s*\{/g;

/** The quiet look (<html data-look="quiet">): light table, dark under the media query, dark forced. */
const QUIET_LIGHT_RE = /:root:where\(\[data-look=['"]quiet['"]\]\)\s*,\s*\.look-quiet\.scheme-light\s*\{/g;
const QUIET_DARK_MEDIA_RE = /:root:where\(\[data-look=['"]quiet['"]\]:not\(\[data-scheme=['"]light['"]\]\)\)\s*\{/g;
const QUIET_DARK_FORCED_RE =
  /:root:where\(\[data-look=['"]quiet['"]\]\[data-scheme=['"]dark['"]\]\)\s*,\s*\.look-quiet\.scheme-dark\s*\{/g;

const sameToken = (a: Token, b: Token): boolean =>
  a.L === b.L && a.C === b.C && a.hue === b.hue && a.kw === b.kw && a.kb === b.kb && a.alpha === b.alpha;

/** Tokens that hold their value in both schemes: the Old Newspaper skin's fixed paper and ink. */
const schemeFree = (name: string): boolean => name.startsWith('press-');

interface DarkTable {
  /** The dark tokens laid over the light ones, as the cascade does it. */
  tokens: Token[];
  /** The tokens the dark table itself defines. */
  own: Token[];
  /** Why the dark table is not usable as written; empty when it is. */
  problems: string[];
}

/** Reads the dark table, proves its two copies agree and that it redefines every light role. */
function darkTable(css: string, light: Token[], mediaRe: RegExp = DARK_MEDIA_RE, forcedRe: RegExp = DARK_FORCED_RE): DarkTable {
  const media = firstTokenBlock(css, mediaRe);
  const forced = firstTokenBlock(css, forcedRe);
  const problems: string[] = [];
  if (!media.length) problems.push('no dark table under @media (prefers-color-scheme: dark)');
  if (!forced.length) problems.push("no dark table under :root:where([data-scheme='dark'])");
  const forcedByName = new Map(forced.map((t) => [t.name, t]));
  for (const t of media) {
    const other = forcedByName.get(t.name);
    if (!other) problems.push(`--${t.name} is in the media copy of the dark table but not the forced one`);
    else if (!sameToken(t, other)) problems.push(`--${t.name} differs between the two copies of the dark table`);
  }
  for (const t of forced) {
    if (!media.some((m) => m.name === t.name)) problems.push(`--${t.name} is in the forced copy of the dark table but not the media one`);
  }
  const darkNames = new Set(media.map((t) => t.name));
  for (const t of light) {
    if (!schemeFree(t.name) && !darkNames.has(t.name)) problems.push(`--${t.name} is not redefined in the dark table`);
  }
  const merged = new Map(light.map((t) => [t.name, t]));
  for (const t of media) merged.set(t.name, t);
  return { tokens: [...merged.values()], own: media, problems };
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
/** The fourth lobe sits at a fixed +70. */
const H4_OFFSET = 70;
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
          : t.hue === 'h4'
            ? [mod360(baseHue + H4_OFFSET)]
            : [t.hue];
  const kws = t.kw ? KW_RANGE : [1];
  const kbs = t.kb ? KB_RANGE : [1];
  const out: Lin[] = [];
  for (const h of hues) for (const kw of kws) for (const kb of kbs) out.push(oklchToLinear(t.L, t.C * kw * kb, h));
  return out;
}

/** A token's nominal colour (offsets +35 / −45, all multipliers 1), as used for the fallback. */
function nominal(t: Token, baseHue: number): Lin {
  const h = t.hue === 'h' ? baseHue : t.hue === 'h2' ? mod360(baseHue + 35) : t.hue === 'h3' ? mod360(baseHue - 45) : t.hue === 'h4' ? mod360(baseHue + H4_OFFSET) : t.hue;
  return oklchToLinear(t.L, t.C, h);
}

interface Pair {
  fg: string;
  bg: string;
  min: number;
}

/**
 * Required contrast pairs: foreground over background, minimum WCAG ratio. The same pairs hold in
 * both schemes; in dark, surface / accent-strong is the dark button text on the light primary fill,
 * and the alpha tokens are composited over the dark field.
 */
const PAIRS: Pair[] = [
  { fg: 'text', bg: 'bg', min: 4.5 },
  { fg: 'text', bg: 'surface', min: 4.5 },
  { fg: 'text', bg: 'surface-2', min: 4.5 },
  // a recessed well inside a panel (the practice room's answer boxes)
  { fg: 'text', bg: 'well', min: 4.5 },
  { fg: 'text-muted', bg: 'well', min: 4.5 },
  { fg: 'text-faint', bg: 'well', min: 3 },
  { fg: 'text', bg: 'lobe-1', min: 4.5 },
  { fg: 'text', bg: 'lobe-2', min: 4.5 },
  { fg: 'text', bg: 'lobe-3', min: 4.5 },
  { fg: 'text', bg: 'lobe-4', min: 4.5 },
  { fg: 'text-muted', bg: 'bg', min: 4.5 },
  { fg: 'text-muted', bg: 'surface-2', min: 4.5 },
  // Radar numerals and labels (muted) sit over the ambient lobes.
  { fg: 'text-muted', bg: 'lobe-1', min: 4.5 },
  { fg: 'text-muted', bg: 'lobe-2', min: 4.5 },
  { fg: 'text-muted', bg: 'lobe-3', min: 4.5 },
  { fg: 'text-muted', bg: 'lobe-4', min: 4.5 },
  // --text-faint is for placeholders and disabled text only: it does not reach 4.5:1 anywhere
  // (worst about 3.6:1 over the lobes), so it is held at the 3:1 floor and must not carry data.
  { fg: 'text-faint', bg: 'bg', min: 3 },
  { fg: 'text-faint', bg: 'surface-2', min: 3 },
  { fg: 'text-faint', bg: 'lobe-1', min: 3 },
  { fg: 'text-faint', bg: 'lobe-2', min: 3 },
  { fg: 'text-faint', bg: 'lobe-3', min: 3 },
  { fg: 'text-faint', bg: 'lobe-4', min: 3 },
  { fg: 'accent', bg: 'bg', min: 4.5 },
  { fg: 'surface', bg: 'accent-strong', min: 4.5 },
  { fg: 'danger', bg: 'bg', min: 4.5 },
  { fg: 'accent-ui', bg: 'bg', min: 3 },
  { fg: 'accent-ui', bg: 'surface', min: 3 },
  { fg: 'accent-ui', bg: 'lobe-1', min: 3 },
  { fg: 'accent-ui', bg: 'lobe-2', min: 3 },
  { fg: 'accent-ui', bg: 'lobe-3', min: 3 },
  { fg: 'accent-ui', bg: 'lobe-4', min: 3 },
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

function check(tokens: Token[], pairList: Pair[] = PAIRS): { pairs: PairResult[]; gamut: GamutFailure[]; missing: string[] } {
  const byName = new Map(tokens.map((t) => [t.name, t]));
  const missing = [...new Set(pairList.flatMap((p) => [p.fg, p.bg]))].filter((n) => !byName.has(n));
  const gamut: GamutFailure[] = [];
  const results: PairResult[] = pairList.filter((p) => byName.has(p.fg) && byName.has(p.bg)).map((p) => ({
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

/**
 * The quiet look's pairs: today's, minus --line-strong over --bg. Quiet hairlines (.84 light, .40 dark) are
 * region edges, not control borders, so the 3:1 non-text floor does not hold for them by design; a control in
 * the quiet look must be told apart by its --well fill and focus ring (--accent-ui, still held at 3:1).
 */
const QUIET_PAIRS: Pair[] = PAIRS.filter((p) => !(p.fg === 'line-strong' && p.bg === 'bg'));

/** Share of the smallest sRGB chroma (over all hues) a quiet role may use. */
const HEADROOM = 0.97;

/** Largest in-gamut chroma at L and hue (bisection). */
function maxChroma(L: number, hue: number): number {
  let lo = 0;
  let hi = 0.5;
  for (let i = 0; i < 30; i++) {
    const mid = (lo + hi) / 2;
    if (inGamut(oklchToLinear(L, mid, hue))) lo = mid;
    else hi = mid;
  }
  return lo;
}

/** Quiet roles whose chroma is above HEADROOM x the minimum sRGB chroma across hues at their lightness. */
function headroomFailures(tokens: Token[]): string[] {
  const out: string[] = [];
  const memo = new Map<number, number>();
  for (const t of tokens) {
    if (schemeFree(t.name) || t.alpha < 1 && t.name.startsWith('lobe-')) continue;
    const hues = typeof t.hue === 'number' ? [t.hue] : [...Array(360).keys()];
    const key = t.L * 1000 + (typeof t.hue === 'number' ? t.hue / 1000 : 0);
    let floor = memo.get(key);
    if (floor === undefined) {
      floor = Math.min(...hues.map((h) => maxChroma(t.L, h)));
      memo.set(key, floor);
    }
    if (t.C > HEADROOM * floor) out.push(`--${t.name}: C ${t.C} at L ${t.L} exceeds ${HEADROOM} x ${floor.toFixed(4)} = ${(HEADROOM * floor).toFixed(4)}`);
  }
  return out;
}

/* ----------------------------------------------------------------------------
 * The cursor light (src/light/palette.ts): the viscous wake's dye, at its peak
 * alpha, composited over the field and every lobe, under every text role that
 * sits on the bare field. The shader reads the same numbers, so what is proved
 * here is what is drawn. Every pixel of the wake is a weighted blend of the head
 * and tail tones at hues between them, so the check walks three tones (head,
 * midway, tail) across the whole hue range: the head's offset and wander down to
 * the tail's. The peak alpha is the strongest the dye is ever drawn.
 * ------------------------------------------------------------------------- */

/**
 * Foreground roles on the bare field, and the floor each keeps over the dye.
 * --line-strong is not among them: on Home it only draws the drop zone's dashed border and the
 * field underline, both inside the opaque panel the light never shows through.
 */
const LIGHT_PAIRS: { fg: string; min: number }[] = [
  { fg: 'text', min: 4.5 },
  { fg: 'text-muted', min: 4.5 },
  // the italic headline line (40-56 px, large text) and the drop-zone icon: the 3:1 floor
  { fg: 'accent', min: 3 },
  { fg: 'accent-ui', min: 3 },
];

/** A spotlight under the wake: OKLCH tone and opacity. */
interface Spot {
  L: number;
  C: number;
  alpha: number;
}

interface DyeTone {
  head: { L: number; C: number };
  tail: { L: number; C: number };
}

/** The dye's samples: three tones across the hue range, in 6 degree steps, plus both ends. */
function dyeOffsets(): { tone: number; off: number }[] {
  const hi = LIGHT.wake.hue.head + MAX_WANDER;
  const lo = LIGHT.wake.hue.head - MAX_WANDER + LIGHT.wake.hue.tail;
  const out: { tone: number; off: number }[] = [];
  for (const tone of [0, 0.5, 1]) for (let off = hi; off >= lo - 1e-9; off -= 6) out.push({ tone, off });
  out.push({ tone: 0, off: lo }, { tone: 1, off: lo }, { tone: 0, off: hi }, { tone: 1, off: hi });
  return out;
}

/**
 * The shader's gamut fit, step for step (src/light/shader.ts, oklch()): lightness and hue are kept and chroma
 * is cut by 0.72 up to four times until the colour is inside sRGB. Returns the fitted colour and the cuts used.
 */
function fitToGamut(L: number, C: number, hue: number): { lin: Lin; cuts: number } {
  let c = C;
  let lin = oklchToLinear(L, c, hue);
  let cuts = 0;
  while (cuts < 4 && !inGamut(lin)) {
    c *= 0.72;
    lin = oklchToLinear(L, c, hue);
    cuts++;
  }
  return { lin, cuts };
}

/** The dye colours (as the shader fits them into sRGB) at a base hue, and the dye composited over every background. */
function dyeColours(
  baseHue: number,
  dye: DyeTone,
  peak: number,
  byName: Map<string, Token>,
  field: Lin[],
  spot: Spot | null,
): { dyes: Lin[]; over: Lin[]; cuts: number } {
  let cuts = 0;
  const dyes = dyeOffsets().map(({ tone, off }) => {
    const hue = mod360(baseHue + off);
    const L = dye.head.L + (dye.tail.L - dye.head.L) * tone;
    const C = dye.head.C + (dye.tail.C - dye.head.C) * tone;
    const fit = fitToGamut(L, C * warmEasing(hue), hue);
    cuts = Math.max(cuts, fit.cuts);
    return fit.lin;
  });
  // what the dye sits on: the field, and each lobe over the field
  const backs: Lin[] = [...field];
  for (const name of ['lobe-1', 'lobe-2', 'lobe-3', 'lobe-4']) {
    const t = byName.get(name);
    if (!t) continue;
    for (const v of variants(t, baseHue)) backs.push(...(t.alpha >= 1 ? [v] : field.map((f) => composite(v, t.alpha, f))));
  }
  // the quiet dark spotlight lifts the field under the wake: text is also measured over it, bare and under the dye
  if (spot) {
    const lit = backs.map((b) => composite(oklchToLinear(spot.L, spot.C, 0), spot.alpha, b));
    backs.push(...lit);
  }
  return { dyes, over: dyes.flatMap((d) => backs.map((b) => composite(d, peak, b))).concat(spot ? backs : []), cuts };
}

function checkLight(tokens: Token[], dye: DyeTone, peak: number, spot: Spot | null = null): { pairs: PairResult[]; gamut: number[]; fitted: number; deepest: number } {
  const byName = new Map(tokens.map((t) => [t.name, t]));
  const results: PairResult[] = LIGHT_PAIRS.filter((p) => byName.has(p.fg)).map((p) => ({
    fg: p.fg,
    bg: 'dye',
    min: p.min,
    worst: Infinity,
    worstHue: -1,
  }));
  const gamut: number[] = [];
  let fitted = 0;
  let deepest = 0;
  const bg = byName.get('bg')!;
  for (let hue = 0; hue < 360; hue++) {
    const field = variants(bg, hue);
    const { dyes, over, cuts } = dyeColours(hue, dye, peak, byName, field, spot);
    if (!dyes.every(inGamut)) gamut.push(hue);
    if (cuts > 0) fitted++;
    deepest = Math.max(deepest, cuts);
    for (const r of results) {
      const t = byName.get(r.fg)!;
      // an ink with alpha is measured as seen, over the field
      const inks = variants(t, hue).flatMap((v) => (t.alpha >= 1 ? [v] : field.map((f) => composite(v, t.alpha, f))));
      for (const f of inks) {
        for (const b of over) {
          const ratio = contrast(f, b);
          if (ratio < r.worst) {
            r.worst = ratio;
            r.worstHue = hue;
          }
        }
      }
    }
  }
  return { pairs: results, gamut, fitted, deepest };
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

/** One hex declaration per token for one base hue at k = kw = kb = 1, each line indented by `pad`. */
function fallbackLines(tokens: Token[], hue: number, pad: string): string {
  return tokens
    .map((t) => {
      const lin = nominal(t, hue);
      return `${pad}--${t.name}: ${t.alpha >= 1 ? toHex(lin) : toRgba(lin, t.alpha)};`;
    })
    .join('\n');
}

/** The ':root { ... }' hex block for one base hue. The press tokens are left out: they have their own hex block. */
function fallbackBlock(tokens: Token[], hue: number): string {
  return `:root {\n${fallbackLines(
    tokens.filter((t) => !schemeFree(t.name)),
    hue,
    '  ',
  )}\n}`;
}

/** The dark hex blocks for one base hue: the media copy, then the forced copy, as tokens.css lays them out. */
function darkFallbackBlocks(dark: Token[], hue: number): string {
  return [
    '@media (prefers-color-scheme: dark) {',
    "  :root:where(:not([data-scheme='light'])) {",
    fallbackLines(dark, hue, '    '),
    '  }',
    '}',
    ":root:where([data-scheme='dark']) {",
    fallbackLines(dark, hue, '  '),
    '}',
  ].join('\n');
}

/** The quiet look's tables as the cascade builds them: today's light (for the fixed press tokens) under the quiet ones. */
function quietTables(css: string, today: Token[]): { light: { own: Token[]; tokens: Token[] }; dark: DarkTable } {
  const own = firstTokenBlock(css, QUIET_LIGHT_RE);
  const merged = new Map(today.map((t) => [t.name, t]));
  for (const t of own) merged.set(t.name, t);
  const tokens = [...merged.values()];
  return { light: { own, tokens }, dark: darkTable(css, tokens, QUIET_DARK_MEDIA_RE, QUIET_DARK_FORCED_RE) };
}

/** The quiet hex blocks for one base hue, laid out like their oklch tables (light, dark media, dark forced). */
function quietFallbackBlocks(light: Token[], dark: Token[], hue: number): string {
  return [
    ":root:where([data-look='quiet']) {",
    fallbackLines(light, hue, '  '),
    '}',
    '@media (prefers-color-scheme: dark) {',
    "  :root:where([data-look='quiet']:not([data-scheme='light'])) {",
    fallbackLines(dark, hue, '    '),
    '  }',
    '}',
    ":root:where([data-look='quiet'][data-scheme='dark']) {",
    fallbackLines(dark, hue, '  '),
    '}',
  ].join('\n');
}

/* ----------------------------------------------------------------------------
 * CLI
 * ------------------------------------------------------------------------- */

const HEADER = ['pair', 'worst', 'at hue', 'min', 'result'];

function printTable(rows: string[][]): void {
  const widths = HEADER.map((h, i) => Math.max(h.length, ...rows.map((row) => row[i].length)));
  const fmt = (row: string[]) => row.map((c, i) => c.padEnd(widths[i])).join('  ');
  console.log(fmt(HEADER));
  console.log(widths.map((w) => '-'.repeat(w)).join('  '));
  for (const row of rows) console.log(fmt(row));
}

/** Runs the pair and gamut checks for one scheme's tokens and prints them; returns true when all pass. */
function reportScheme(scheme: string, tokens: Token[], pairList: Pair[] = PAIRS): boolean {
  const { pairs, gamut, missing } = check(tokens, pairList);
  let ok = true;
  console.log(`palette-check, ${scheme}: ${tokens.length} tokens, 360 hues\n`);
  printTable(
    pairs.map((r) => {
      const pass = r.worst >= r.min;
      ok &&= pass;
      return [`${r.fg} / ${r.bg}`, r.worst.toFixed(2), String(r.worstHue), r.min.toFixed(1), pass ? 'pass' : 'FAIL'];
    }),
  );

  if (missing.length) {
    ok = false;
    console.log(`\nMissing tokens: ${missing.join(', ')}`);
  }

  if (gamut.length) {
    ok = false;
    const byToken = new Map<string, number[]>();
    for (const g of gamut) byToken.set(g.name, [...(byToken.get(g.name) ?? []), g.hue]);
    console.log('\nOut of sRGB gamut:');
    for (const [name, hues] of byToken) {
      console.log(`  --${name}: ${hues.length} hue(s), e.g. ${hues.slice(0, 6).join(', ')}${hues.length > 6 ? ', …' : ''}`);
    }
  } else {
    console.log('\nGamut: every token inside sRGB at every hue.');
  }
  console.log();
  return ok;
}

function main(): number {
  const css = readFileSync(TOKENS_PATH, 'utf8');
  const tokens = parseTokens(css);
  if (tokens.length === 0) {
    console.error(`palette-check: no oklch tokens found in ${TOKENS_PATH}`);
    return 1;
  }
  const dark = darkTable(css, tokens);

  const args = process.argv.slice(2);
  const fallbackAt = args.indexOf('--fallback');
  if (fallbackAt !== -1) {
    const hue = Number(args[fallbackAt + 1]);
    if (!Number.isInteger(hue) || hue < 0 || hue > 359) {
      console.error('palette-check: --fallback needs an integer hue 0..359');
      return 1;
    }
    console.log(fallbackBlock(tokens, hue));
    console.log();
    console.log(darkFallbackBlocks(dark.own, hue));
    // the quiet look's blocks (selectors scoped to <html data-look="quiet">), same hue
    const q = quietTables(css, tokens);
    console.log();
    console.log(quietFallbackBlocks(q.light.own, q.dark.own, hue));
    return 0;
  }

  let failed = !reportScheme('light', tokens);

  if (dark.problems.length) {
    failed = true;
    console.log('Dark table:');
    for (const p of dark.problems) console.log(`  ${p}`);
    console.log();
  }
  failed = !reportScheme('dark', dark.tokens) || failed;

  // The quiet look: its light table, its dark table (two copies), chroma headroom, the dye.
  const quiet = quietTables(css, tokens);
  console.log('=== quiet look (data-look="quiet") ===\n');
  if (!quiet.light.own.length) {
    failed = true;
    console.log('No oklch table found for the quiet light look.\n');
  }
  failed = !reportScheme('quiet light', quiet.light.tokens, QUIET_PAIRS) || failed;
  if (quiet.dark.problems.length) {
    failed = true;
    console.log('Quiet dark table:');
    for (const p of quiet.dark.problems) console.log(`  ${p}`);
    console.log();
  }
  failed = !reportScheme('quiet dark', quiet.dark.tokens, QUIET_PAIRS) || failed;
  // Quiet dark draws faint lobes since 2026-11-07 (design.md §7a); they are checked like any lobe by the pairs above.
  for (const [scheme, table] of [['light', quiet.light.tokens], ['dark', quiet.dark.tokens]] as const) {
    const over = headroomFailures(table.filter((t) => quiet[scheme === 'light' ? 'light' : 'dark'].own.some((o) => o.name === t.name)));
    if (over.length) {
      failed = true;
      console.log(`Quiet ${scheme}: chroma above ${HEADROOM} x the minimum sRGB chroma:`);
      for (const o of over) console.log(`  ${o}`);
    } else {
      console.log(`Quiet ${scheme}: every role's chroma is within ${HEADROOM} x the minimum sRGB chroma across hues.`);
    }
  }
  console.log();

  for (const [scheme, table, dye, peak, spot] of [
    ['light', tokens, LIGHT.wake.tone.light, LIGHT.wake.alpha.light, null],
    ['dark', dark.tokens, LIGHT.wake.tone.dark, LIGHT.wake.alpha.dark, null],
    ['quiet light', quiet.light.tokens, wakePalette('quiet', false).head && wakePalette('quiet', false), wakePalette('quiet', false).alpha, wakePalette('quiet', false).spot],
    ['quiet dark', quiet.dark.tokens, wakePalette('quiet', true), wakePalette('quiet', true).alpha, wakePalette('quiet', true).spot],
  ] as const) {
    const light = checkLight(table, dye, peak, spot);
    console.log(
      `cursor light, ${scheme} scheme: dye head L ${dye.head.L} C ${dye.head.C}, tail L ${dye.tail.L} C ${dye.tail.C}, peak alpha ${peak}, hue +${LIGHT.wake.hue.head} (wander ${MAX_WANDER}) then ${LIGHT.wake.hue.tail} round
`,
    );
    printTable(
      light.pairs.map((r) => {
        const ok = r.worst >= r.min;
        failed ||= !ok;
        return [`${r.fg} / ${r.bg}`, r.worst.toFixed(2), String(r.worstHue), r.min.toFixed(1), ok ? 'pass' : 'FAIL'];
      }),
    );
    if (light.gamut.length) {
      failed = true;
      console.log(`
Dye (${scheme}) still out of sRGB after the shader's gamut fit at ${light.gamut.length} hue(s), e.g. ${light.gamut.slice(0, 6).join(', ')}`);
    } else {
      console.log(`
Dye (${scheme}) inside sRGB at every hue after the shader's gamut fit (chroma cut at ${light.fitted} of 360 base hues, at most ${light.deepest} cut(s) of x0.72).
`);
    }
  }

  console.log(failed ? '\nFAILED' : '\nOK');
  return failed ? 1 : 0;
}

process.exit(main());
