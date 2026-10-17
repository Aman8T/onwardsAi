/*
 * Build check — proves the production bundle still carries the living gradient.
 *
 * Reads dist/index.html and dist/assets/*.css after `vite build` and asserts that
 * esbuild's CSS pass left the oklch tokens, the @property registrations and the
 * @supports block intact, that the dark table survived under its media query and
 * its data-scheme selector, that no shadow slipped through, and that the HTML has
 * the light-dark colour-scheme, the inline boot script (hue and scheme) before any
 * stylesheet, and the ambient layer. Zero dependencies; run with `npx tsx scripts/build-check.ts`.
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

const DIST = resolve(process.cwd(), 'dist');
const MIN_HUE_COLOURS = 20;

interface Check {
  name: string;
  ok: boolean;
  detail?: string;
}

/**
 * Counts oklch() colours whose arguments reference --h / --h2 / --h3.
 * Paren-aware, because every token nests calc(… var(--k) …) before the hue.
 */
function countHueColours(css: string): number {
  let count = 0;
  let from = 0;
  for (;;) {
    const start = css.indexOf('oklch(', from);
    if (start === -1) return count;
    let depth = 0;
    let end = start + 5;
    for (; end < css.length; end++) {
      if (css[end] === '(') depth++;
      else if (css[end] === ')' && --depth === 0) break;
    }
    if (/var\(--h[23]?\)/.test(css.slice(start, end))) count++;
    from = end + 1;
  }
}

function main(): number {
  const htmlPath = join(DIST, 'index.html');
  const assetsDir = join(DIST, 'assets');
  if (!existsSync(htmlPath) || !existsSync(assetsDir)) {
    console.error('build-check: dist/ is missing — run `vite build` first');
    return 1;
  }
  const html = readFileSync(htmlPath, 'utf8');
  const cssFiles = readdirSync(assetsDir).filter((f) => f.endsWith('.css'));
  const css = cssFiles.map((f) => readFileSync(join(assetsDir, f), 'utf8')).join('\n');

  const hueColours = countHueColours(css);
  const bootAt = html.indexOf('tayyari.hue0');
  const stylesheetAt = html.search(/<link[^>]*rel="stylesheet"/);

  const checks: Check[] = [
    { name: 'stylesheet emitted', ok: cssFiles.length > 0, detail: cssFiles.join(', ') || 'no dist/assets/*.css' },
    { name: '@property --h registered', ok: /@property\s+--h\s*\{/.test(css) },
    {
      name: `at least ${MIN_HUE_COLOURS} oklch colours driven by --h`,
      ok: hueColours >= MIN_HUE_COLOURS,
      detail: `${hueColours} found`,
    },
    { name: '@supports (color: oklch(…)) block', ok: /@supports\s*\(\s*color\s*:\s*oklch\(/.test(css) },
    { name: 'no box-shadow', ok: !css.includes('box-shadow') && !html.includes('box-shadow') },
    { name: 'meta color-scheme light dark', ok: /<meta\s+name="color-scheme"\s+content="light dark"\s*\/?>/.test(html) },
    {
      name: 'dark table under prefers-color-scheme and data-scheme',
      ok: /@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)/.test(css) && /\[data-scheme=["']?dark["']?\]/.test(css),
    },
    { name: 'boot script writes data-scheme', ok: html.includes('tayyari.scheme') },
    {
      name: 'inline boot script before the first stylesheet',
      ok: bootAt !== -1 && stylesheetAt !== -1 && bootAt < stylesheetAt,
      detail: bootAt === -1 ? 'boot script missing' : stylesheetAt === -1 ? 'no stylesheet link' : undefined,
    },
    { name: 'ambient layer present', ok: html.includes('<div class="ambient"') },
  ];

  let failed = false;
  for (const c of checks) {
    failed ||= !c.ok;
    console.log(`${c.ok ? 'pass' : 'FAIL'}  ${c.name}${c.detail ? `  (${c.detail})` : ''}`);
  }
  console.log(failed ? '\nbuild-check: FAILED' : '\nbuild-check: OK');
  return failed ? 1 : 0;
}

process.exit(main());
