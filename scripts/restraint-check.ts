/*
 * Restraint check — lints the visual layer for anything the calm theme forbids.
 *
 * Scans src/**\/*.{css,ts,tsx} and index.html. Zero dependencies.
 * Run with `npx tsx scripts/restraint-check.ts`; exits 1 and lists file:line on:
 *   - box-shadow, text-shadow, drop-shadow(), backdrop-filter, blur(<length>)
 *   - any border (or side / width longhand) thicker than 1px — outline is allowed
 *   - colour literals (#hex, rgb/rgba/hsl/hsla, color-mix; in stylesheets also literal
 *     oklch/oklab/lab/lch/hwb/color) outside src/theme/tokens.css and the favicon line of index.html
 *   - @keyframes that animate paint or layout (box-shadow, filter, background-position,
 *     stroke-dashoffset, offset-distance, width, height, left, top)
 *   - `infinite` animations outside .ch-typing, .ch-caret, .ch-live, .ch-tool.is-busy
 *   - a transition naming a hue property (--h, --h2, --h3, --k, --kw, --kb) or `all`,
 *     which would animate the registered hue properties on every 7 s step
 *   - the old theme files still existing
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

const ROOT = process.cwd();
const SCAN_EXTS = new Set(['.css', '.ts', '.tsx']);
const TOKENS_FILE = 'src/theme/tokens.css';
const INFINITE_ALLOWLIST = ['.ch-typing', '.ch-caret', '.ch-live', '.ch-tool.is-busy'];
const DELETED_FILES = [
  'src/styles/keycap.css',
  'src/styles/graph.css',
  'src/components/Background.tsx',
  'src/components/Stage.tsx',
  'src/views/chat/Orb.tsx',
];

interface Violation {
  file: string;
  line: number;
  rule: string;
  snippet: string;
}

/* ----------------------------------------------------------------------------
 * Helpers
 * ------------------------------------------------------------------------- */

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (SCAN_EXTS.has(full.slice(full.lastIndexOf('.')))) out.push(full);
  }
  return out;
}

const toPosix = (p: string): string => relative(ROOT, p).split('\\').join('/');

function lineOf(text: string, index: number): number {
  let line = 1;
  for (let i = 0; i < index; i++) if (text.charCodeAt(i) === 10) line++;
  return line;
}

function snippetAt(text: string, index: number): string {
  const start = text.lastIndexOf('\n', index) + 1;
  const end = text.indexOf('\n', index);
  return text.slice(start, end === -1 ? undefined : end).trim().slice(0, 100);
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

/** The selector of the rule enclosing `index`, or '' when there is none (e.g. a JS object). */
function enclosingSelector(text: string, index: number): string {
  let depth = 0;
  let open = -1;
  for (let i = index - 1; i >= 0; i--) {
    if (text[i] === '}') depth++;
    else if (text[i] === '{') {
      if (depth === 0) {
        open = i;
        break;
      }
      depth--;
    }
  }
  if (open === -1) return '';
  const start = Math.max(text.lastIndexOf('{', open - 1), text.lastIndexOf('}', open - 1), text.lastIndexOf(';', open - 1));
  return text
    .slice(start + 1, open)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .trim();
}

/** True when a declaration value carries a length thicker than 1px. */
function thickerThanOnePx(value: string): boolean {
  for (const m of value.matchAll(/(\d*\.?\d+)(px|r?em)?(?![\w.])/g)) {
    const n = Number(m[1]);
    const unit = m[2];
    if (unit === 'px' || unit === undefined ? n > 1 : n > 0.0625) return true;
  }
  return false;
}

/* ----------------------------------------------------------------------------
 * Rules
 * ------------------------------------------------------------------------- */

type Rule = (text: string, file: string, report: (index: number, rule: string) => void) => void;

const effects: Rule = (text, _file, report) => {
  const re = /\b(?:box-shadow|text-shadow|backdrop-filter|boxShadow|textShadow|backdropFilter)\b|drop-shadow\(|blur\(\s*[\d.]/g;
  for (const m of text.matchAll(re)) report(m.index, `forbidden effect: ${m[0].replace(/\(.*$/, '(')}`);
};

const borders: Rule = (text, _file, report) => {
  const css =
    /\bborder(?:-(?:top|right|bottom|left|inline|block|inline-start|inline-end|block-start|block-end))?(?:-width)?\s*:\s*([^;}\n]*)/g;
  const js = /\bborder(?:Top|Right|Bottom|Left|Inline|Block)?(?:Width)?\s*:\s*([^,;}\n]*)/g;
  for (const re of [css, js]) {
    for (const m of text.matchAll(re)) if (thickerThanOnePx(m[1])) report(m.index, 'border thicker than 1px');
  }
};

const colourLiterals: Rule = (text, file, report) => {
  if (file === TOKENS_FILE) return;
  // Literal modern colour functions are only flagged in stylesheets: in scripts an
  // oklch() string is a feature probe (CSS.supports), not a colour.
  const re = file.endsWith('.css')
    ? /(?<![\w&#])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix)\(/g
    : /(?<![\w&#])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b|\b(?:rgba?|hsla?|color-mix)\(/g;
  for (const m of text.matchAll(re)) {
    if (file === 'index.html' && snippetAt(text, m.index).includes('rel="icon"')) continue;
    report(m.index, `colour literal ${m[0]}`);
  }
};

const paintKeyframes: Rule = (text, _file, report) => {
  const paint = /(?:^|[;{\s])(box-shadow|filter|background-position|stroke-dashoffset|offset-distance|width|height|left|top)\s*:/;
  for (const m of text.matchAll(/@keyframes\s+[\w-]+\s*\{/g)) {
    const open = m.index + m[0].length - 1;
    const close = closingBrace(text, open);
    const body = text.slice(open, close === -1 ? undefined : close);
    const hit = paint.exec(body);
    if (hit) report(m.index, `@keyframes animates ${hit[1]}`);
  }
};

const infiniteAnimations: Rule = (text, _file, report) => {
  for (const m of text.matchAll(/\banimation(?:-iteration-count|IterationCount)?\s*:\s*([^;}]*)/g)) {
    if (!/\binfinite\b/.test(m[1])) continue;
    const selector = enclosingSelector(text, m.index);
    if (INFINITE_ALLOWLIST.some((allowed) => selector.includes(allowed))) continue;
    report(m.index, `infinite animation outside the allowlist${selector ? ` (${selector.slice(0, 40)})` : ''}`);
  }
};

const hueTransitions: Rule = (text, _file, report) => {
  for (const m of text.matchAll(/\btransition(?:-property|Property)?\s*:\s*([^;}]*)/g)) {
    if (/--(?:h[23]?|kw|kb|k)\b/.test(m[1])) report(m.index, 'transition names a hue property');
    else if (/(?:^|[\s,'"`])all\b/.test(m[1])) report(m.index, 'transition: all would animate the registered hue properties');
  }
};

const RULES: Rule[] = [effects, borders, colourLiterals, paintKeyframes, infiniteAnimations, hueTransitions];

/* ----------------------------------------------------------------------------
 * CLI
 * ------------------------------------------------------------------------- */

function main(): number {
  const files = [...walk(resolve(ROOT, 'src')), resolve(ROOT, 'index.html')].filter(existsSync);
  const violations: Violation[] = [];

  for (const full of files) {
    const file = toPosix(full);
    const text = readFileSync(full, 'utf8');
    const report = (index: number, rule: string) =>
      violations.push({ file, line: lineOf(text, index), rule, snippet: snippetAt(text, index) });
    for (const rule of RULES) rule(text, file, report);
  }

  for (const file of DELETED_FILES) {
    if (existsSync(resolve(ROOT, file))) violations.push({ file, line: 0, rule: 'old theme file still exists', snippet: '' });
  }

  violations.sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line);
  for (const v of violations) {
    console.log(`${v.file}:${v.line}  ${v.rule}${v.snippet ? `  — ${v.snippet}` : ''}`);
  }

  const fileCount = new Set(violations.map((v) => v.file)).size;
  if (violations.length) {
    console.log(`\nrestraint-check: ${violations.length} violation(s) in ${fileCount} file(s)`);
    return 1;
  }
  console.log(`restraint-check: clean (${files.length} files)`);
  return 0;
}

process.exit(main());
