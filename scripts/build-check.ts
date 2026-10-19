/*
 * Build check — proves the production bundle still carries the living gradient.
 *
 * Reads dist/index.html and dist/assets/*.css after `vite build` and asserts that
 * esbuild's CSS pass left the oklch tokens, the @property registrations and the
 * @supports block intact, that the dark table survived under its media query and
 * its data-scheme selector, that no shadow slipped through, and that the HTML has
 * the light-dark colour-scheme, the inline boot script (hue and scheme) before any
 * stylesheet, and the ambient layer. It also proves the crawlable pages that scripts/build-pages.tsx writes: every
 * page of src/site/links.ts as a file with one h1, a canonical link, a description of at most 160 characters,
 * share tags and structured data (FAQ data that matches the questions shown), the Home and Pricing shells with
 * their own heads, the 404 page, the sitemap, robots.txt and the shared stylesheet. Run it after
 * `vite build && tsx scripts/build-pages.tsx` (npm run check:build does both): `npx tsx scripts/build-check.ts`.
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { SITE_PAGES } from '../src/site/links';
import { HOME_HEAD, MAX_DESCRIPTION, PRICING_HEAD, SITE_LINE, absoluteUrl, resolveSiteUrl } from '../src/site/seo';
import type { AppHead } from '../src/site/seo';

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

/* ----------------------------------------------------------------------------
 * The crawlable pages
 * ------------------------------------------------------------------------- */

const SITE_URL = resolveSiteUrl(process.env.SITE_URL);
/** Words a content page needs to be more than a stub. */
const MIN_PAGE_WORDS = 400;
/** App routes the pages may link to besides the pages of SITE_PAGES. */
const APP_PATHS = ['/match', '/practice', '/discover'];

const decode = (text: string): string =>
  text.replace(/&quot;/g, '"').replace(/&#x27;|&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');

/** The words a reader sees: scripts and no-script blocks removed, tags removed, entities decoded. */
const visibleText = (html: string): string =>
  decode(
    html
      .replace(/<script[\s\S]*?<\/script>/g, ' ')
      .replace(/<noscript[\s\S]*?<\/noscript>/g, ' ')
      .replace(/<[^>]*>/g, ' '),
  )
    .replace(/\s+/g, ' ')
    .trim();

function metaContent(html: string, attr: 'name' | 'property', key: string): string | undefined {
  const m = new RegExp(`<meta\\s+${attr}="${key}"\\s+content="([^"]*)"`).exec(html);
  return m ? decode(m[1]) : undefined;
}

const canonicalOf = (html: string): string | undefined => /<link rel="canonical" href="([^"]*)"/.exec(html)?.[1];

function titleOf(html: string): string | undefined {
  const m = /<title>([\s\S]*?)<\/title>/.exec(html);
  return m ? decode(m[1]) : undefined;
}

type Node = Record<string, unknown>;

/** The nodes of the page's JSON-LD (flattening an @graph), or null when there is none or it does not parse. */
function structuredData(html: string): Node[] | null {
  const m = /<script type="application\/ld\+json">([\s\S]*?)<\/script>/.exec(html);
  if (!m) return null;
  try {
    const data = JSON.parse(m[1]) as Node;
    return (data['@graph'] as Node[] | undefined) ?? [data];
  } catch {
    return null;
  }
}

const typesOf = (nodes: Node[] | null): string[] => (nodes ?? []).map((n) => String(n['@type']));

function readIfExists(relative: string): string | null {
  const file = join(DIST, relative);
  return existsSync(file) ? readFileSync(file, 'utf8') : null;
}

/** Head checks shared by the content pages and the two app shells. */
function headChecks(label: string, html: string, path: string, expect?: AppHead): Check[] {
  const title = titleOf(html);
  const description = metaContent(html, 'name', 'description');
  const canonical = canonicalOf(html);
  const nodes = structuredData(html);
  const wantCanonical = absoluteUrl(SITE_URL, path);
  return [
    { name: `${label}: title`, ok: !!title && (expect === undefined || title === expect.title), detail: title ?? 'no <title>' },
    {
      name: `${label}: meta description of 1 to ${MAX_DESCRIPTION} characters`,
      ok: !!description && description.length <= MAX_DESCRIPTION && (expect === undefined || description === expect.description),
      detail: description === undefined ? 'missing' : `${description.length} characters`,
    },
    { name: `${label}: canonical link`, ok: canonical === wantCanonical, detail: `${canonical ?? 'missing'}, want ${wantCanonical}` },
    {
      name: `${label}: Open Graph and Twitter tags`,
      ok:
        metaContent(html, 'property', 'og:title') === title &&
        metaContent(html, 'property', 'og:description') === description &&
        metaContent(html, 'property', 'og:url') === wantCanonical &&
        metaContent(html, 'name', 'twitter:card') !== undefined &&
        metaContent(html, 'name', 'twitter:title') === title,
    },
    {
      name: `${label}: structured data names the Organization and the WebSite`,
      ok: typesOf(nodes).includes('Organization') && typesOf(nodes).includes('WebSite'),
      detail: nodes === null ? 'no valid JSON-LD' : typesOf(nodes).join(', '),
    },
  ];
}

/** One line for a page: ok when every check passed, else the names of the ones that did not. */
function group(label: string, checks: Check[]): Check {
  const failed = checks.filter((c) => !c.ok);
  return {
    name: label,
    ok: failed.length === 0,
    detail: failed.length === 0 ? `${checks.length} checks` : failed.map((c) => `${c.name}${c.detail ? ` (${c.detail})` : ''}`).join('; '),
  };
}

function contentPageChecks(path: string, seen: { titles: Map<string, string>; descriptions: Map<string, string> }): Check {
  const known = new Set([...SITE_PAGES.map((p) => p.path), ...APP_PATHS]);
  const file = `${path.slice(1)}/index.html`;
  const html = readIfExists(file);
  if (html === null) return { name: `page ${path}`, ok: false, detail: `dist/${file} is missing` };

  const nodes = structuredData(html);
  const text = visibleText(/<main[\s\S]*<\/main>/.exec(html)?.[0] ?? html);
  const pageText = visibleText(html);
  const words = text.split(' ').length;
  const faq = (nodes ?? []).find((n) => n['@type'] === 'FAQPage') as { mainEntity?: { name: string; acceptedAnswer: { text: string } }[] } | undefined;
  const questions = faq?.mainEntity ?? [];
  const title = titleOf(html) ?? '';
  const description = metaContent(html, 'name', 'description') ?? '';
  const hrefs = [...html.matchAll(/<a [^>]*href="(\/[^"#?]*)/g)].map((m) => m[1]);

  const checks: Check[] = [
    ...headChecks(path, html, path),
    { name: `${path}: exactly one h1`, ok: (html.match(/<h1[\s>]/g) ?? []).length === 1 },
    { name: `${path}: html lang, viewport, and no script file`, ok: html.includes('<html lang="en">') && html.includes('name="viewport"') && !/<script[^>]*\ssrc=/.test(html) },
    { name: `${path}: at least ${MIN_PAGE_WORDS} words of text`, ok: words >= MIN_PAGE_WORDS, detail: `${words} words` },
    { name: `${path}: structured data has a BreadcrumbList and a FAQPage`, ok: typesOf(nodes).includes('BreadcrumbList') && typesOf(nodes).includes('FAQPage') },
    {
      name: `${path}: every FAQPage question and answer is shown on the page`,
      ok: questions.length >= 3 && questions.every((q) => text.includes(q.name) && text.includes(q.acceptedAnswer.text)),
      detail: `${questions.length} questions`,
    },
    { name: `${path}: footer line and a link to every page`, ok: pageText.includes(SITE_LINE) && SITE_PAGES.every((p) => html.includes(`<a href="${p.path}"`)) },
    { name: `${path}: every internal link goes to a known path`, ok: hrefs.every((h) => known.has(h)), detail: hrefs.filter((h) => !known.has(h)).join(', ') || undefined },
    { name: `${path}: stylesheet link`, ok: html.includes('<link rel="stylesheet" href="/site/site.css" />') },
    { name: `${path}: title not used by another page`, ok: !seen.titles.has(title), detail: seen.titles.get(title) },
    { name: `${path}: description not used by another page`, ok: !seen.descriptions.has(description), detail: seen.descriptions.get(description) },
  ];
  if (path === '/how-it-works') checks.push({ name: `${path}: SoftwareApplication data`, ok: typesOf(nodes).includes('SoftwareApplication') });
  seen.titles.set(title, path);
  seen.descriptions.set(description, path);
  return group(`page ${path}`, checks);
}

function shellChecks(label: string, file: string, path: string, head: AppHead): Check {
  const html = readIfExists(file);
  if (html === null) return { name: label, ok: false, detail: `dist/${file} is missing` };
  const bootAt = html.indexOf('tayyari.hue0');
  const firstStylesheet = html.search(/<link[^>]*rel="stylesheet"/);
  return group(label, [
    ...headChecks(label, html, path, head),
    {
      name: `${label}: still the app (module script, app stylesheet, root, boot script first)`,
      ok:
        /<script type="module"[^>]*src="\/assets\//.test(html) &&
        /<link rel="stylesheet"[^>]*href="\/assets\/[^"]*\.css"/.test(html) &&
        html.includes('<div id="root"></div>') &&
        bootAt !== -1 &&
        bootAt < firstStylesheet,
    },
    { name: `${label}: one copy of each generated block`, ok: html.split('<!--seo:begin-->').length === 2 && html.split('<!--seo:noscript:begin-->').length === 2 },
  ]);
}

function pageChecks(): Check[] {
  const seen = { titles: new Map<string, string>(), descriptions: new Map<string, string>() };
  const checks: Check[] = SITE_PAGES.filter((p) => p.kind === 'page').map((p) => contentPageChecks(p.path, seen));

  const notFound = readIfExists('404.html');
  checks.push({
    name: '404.html: an h1, noindex, a link to every page',
    ok: notFound !== null && /<h1[\s>]/.test(notFound) && notFound.includes('content="noindex"') && SITE_PAGES.every((p) => notFound.includes(`<a href="${p.path}"`)),
    detail: notFound === null ? 'dist/404.html is missing' : undefined,
  });

  checks.push(shellChecks('Home shell', 'index.html', '/', HOME_HEAD), shellChecks('Pricing shell', 'pricing/index.html', '/pricing', PRICING_HEAD));

  const sitemap = readIfExists('sitemap.xml');
  const locs = sitemap === null ? [] : [...sitemap.matchAll(/<loc>([^<]*)<\/loc>/g)].map((m) => decode(m[1]));
  const wantLocs = SITE_PAGES.map((p) => absoluteUrl(SITE_URL, p.path));
  checks.push({
    name: 'sitemap.xml lists every page, absolute, and nothing else',
    ok: sitemap !== null && locs.length === wantLocs.length && wantLocs.every((u) => locs.includes(u)),
    detail: sitemap === null ? 'dist/sitemap.xml is missing' : `${locs.length} urls`,
  });

  const robots = readIfExists('robots.txt');
  checks.push({
    name: 'robots.txt: allow all, disallow /api/ and /dev/, a Sitemap line',
    ok:
      robots !== null &&
      /^User-agent: \*$/m.test(robots) &&
      /^Allow: \/$/m.test(robots) &&
      /^Disallow: \/api\/$/m.test(robots) &&
      /^Disallow: \/dev\/$/m.test(robots) &&
      robots.includes(`Sitemap: ${SITE_URL}/sitemap.xml`),
    detail: robots === null ? 'dist/robots.txt is missing' : undefined,
  });

  const css = readIfExists('site/site.css');
  checks.push({
    name: 'site/site.css carries the tokens, the primitives and the page styles, and no box-shadow',
    ok: css !== null && css.includes('@property --h') && css.includes('.btn-primary') && css.includes('.site-header') && !css.includes('box-shadow'),
    detail: css === null ? 'dist/site/site.css is missing' : undefined,
  });

  return checks;
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
    ...pageChecks(),
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
