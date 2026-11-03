/*
 * Build pages — writes the crawlable part of the site into dist/ after `vite build`.
 *
 *   dist/<slug>/index.html   every `page` of src/site/links.ts, as full static HTML (no app script)
 *   dist/404.html            the same frame, "That page does not exist", links to every page
 *   dist/index.html          the app shell for Home, its head rewritten (title, description, canonical, share tags, data)
 *   dist/pricing/index.html  the app shell again with the pricing head; the app's own script and stylesheet stay in place
 *   dist/site/site.css       tokens.css + global.css + site.css in one file, not content-hashed
 *   dist/app-shell.html      the app shell with no canonical and no structured data, `noindex` (for the app's private routes)
 *   dist/sitemap.xml (with a lastmod where git or the file knows one), dist/robots.txt (AI crawlers named), dist/llms.txt
 *
 * The icons, the manifest and the share picture are files of public/, copied into dist/ by `vite build`; this script
 * links them and checks nothing about their pixels. theme-color is read from the quiet look's --bg in tokens.css.
 *
 * The address used in canonical links, share tags and the sitemap is SITE_URL when set, else DEFAULT_SITE_URL.
 * Re-running is safe: what this script added to the two app shells is marked and replaced, not stacked.
 * Run it with `tsx scripts/build-pages.tsx` (npm run build / build:static / check:build do). The JSX of the site
 * modules is compiled by tsx under the project's tsconfig; this file uses createElement because scripts/ is outside it.
 */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { createElement } from 'react';
import type { ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { Article } from '../src/site/Article';
import { Layout } from '../src/site/Layout';
import { NotFound } from '../src/site/NotFound';
import { COMPANY_LINKS, COMPANY_URL, SITE_PAGES } from '../src/site/links';
import { CONTENT_PAGES } from '../src/site/pages';
import { HomeSeed } from '../src/site/seeds/HomeSeed';
import { PRICING_FAQ, PricingSeed } from '../src/site/seeds/PricingSeed';
import {
  HOME_HEAD,
  MAX_DESCRIPTION,
  OG_IMAGE,
  PRICING_HEAD,
  SITE_LINE,
  SITE_NAME,
  absoluteUrl,
  breadcrumbLd,
  documentTitle,
  faqLd,
  organizationLd,
  pageLd,
  resolveSiteUrl,
  softwareLd,
  websiteLd,
} from '../src/site/seo';
import type { AppHead } from '../src/site/seo';

const ROOT = process.cwd();
const DIST = resolve(ROOT, 'dist');
const SITE_URL = resolveSiteUrl(process.env.SITE_URL);

const SEO_BEGIN = '<!--seo:begin-->';
const SEO_END = '<!--seo:end-->';
const NOSCRIPT_BEGIN = '<!--seo:noscript:begin-->';
const NOSCRIPT_END = '<!--seo:noscript:end-->';

function fail(message: string): never {
  console.error(`build-pages: ${message}`);
  process.exit(1);
}

function write(relative: string, content: string): void {
  const full = join(DIST, relative);
  mkdirSync(dirname(full), { recursive: true });
  writeFileSync(full, content, 'utf8');
}

/** Escapes text for an HTML attribute or element. */
function esc(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** One JSON-LD block. `<`, `>` and `&` are written as escapes so nothing in the data can close the script element. */
function jsonLd(graph: object[]): string {
  const json = JSON.stringify({ '@context': 'https://schema.org', '@graph': graph })
    .replace(/</g, '\\u003c')
    .replace(/>/g, '\\u003e')
    .replace(/&/g, '\\u0026');
  return `<script type="application/ld+json">${json}</script>`;
}

/* ----------------------------------------------------------------------------
 * Checks before anything is written
 * ------------------------------------------------------------------------- */

function validateContent(): void {
  const declared = SITE_PAGES.filter((p) => p.kind === 'page').map((p) => p.path);
  const written = CONTENT_PAGES.map((p) => p.path);
  for (const path of declared) if (!written.includes(path)) fail(`${path} is a page in src/site/links.ts but has no module in src/site/pages`);
  for (const path of written) if (!declared.includes(path)) fail(`${path} has a module but is not a "page" in src/site/links.ts`);
  if (new Set(written).size !== written.length) fail('two content pages share a path');

  const titles = new Set<string>();
  const descriptions = new Set<string>();
  const heads: [string, AppHead][] = [
    ['/', HOME_HEAD],
    ['/pricing', PRICING_HEAD],
    ...CONTENT_PAGES.map((p): [string, AppHead] => [p.path, { title: documentTitle(p), description: p.description }]),
  ];
  for (const [path, head] of heads) {
    if (head.description.length === 0 || head.description.length > MAX_DESCRIPTION) {
      fail(`${path}: description is ${head.description.length} characters (1 to ${MAX_DESCRIPTION})`);
    }
    if (titles.has(head.title)) fail(`${path}: title is used by another page`);
    if (descriptions.has(head.description)) fail(`${path}: description is used by another page`);
    titles.add(head.title);
    descriptions.add(head.description);
  }
  for (const page of CONTENT_PAGES) {
    if (page.faq.length < 3 || page.faq.length > 5) fail(`${page.path}: ${page.faq.length} FAQ entries (3 to 5)`);
    if (page.related.length !== 3) fail(`${page.path}: ${page.related.length} related pages (3)`);
    for (const rel of page.related) {
      if (rel === page.path || !written.includes(rel)) fail(`${page.path}: related page ${rel} is itself or not a content page`);
    }
    if (!page.cta.href.startsWith('/')) fail(`${page.path}: the call to action must be a path into the app`);
  }
  // the price placeholders belong to the Pricing shell alone: the server replaces them only there
  if (renderToStaticMarkup(createElement(HomeSeed)).includes('{{')) fail('the Home seed holds a {{placeholder}}');
  const pricing = renderToStaticMarkup(createElement(PricingSeed));
  for (const key of ['pack3', 'pass30']) if (!pricing.includes(`{{price:${key}}}`)) fail(`the Pricing seed has no {{price:${key}}} placeholder`);
  for (const page of CONTENT_PAGES) {
    const text = renderToStaticMarkup(createElement(page.Body)) + JSON.stringify(page.faq);
    if (text.includes('{{')) fail(`${page.path}: a {{placeholder}} in a content page`);
  }
}

/* ----------------------------------------------------------------------------
 * The head
 * ------------------------------------------------------------------------- */

interface Shell {
  html: string;
  /** The icon, apple-touch-icon and manifest links of index.html. */
  icons: string[];
  fonts: string[];
}

/** The two theme-color metas (light, dark) from the quiet look's --bg in tokens.css, the look every page boots in. */
function themeColours(): string[] {
  const css = readFileSync(resolve(ROOT, 'src/theme/tokens.css'), 'utf8');
  const bgOf = (selector: string): string => {
    const at = css.indexOf(selector);
    const m = at === -1 ? null : /--bg:\s*(#[0-9a-fA-F]{6})/.exec(css.slice(at));
    if (!m) fail(`tokens.css has no hex --bg under ${selector}`);
    return m[1];
  };
  const light = bgOf(":root:where([data-look='quiet']) {");
  const dark = bgOf(":root:where([data-look='quiet']:not([data-scheme='light'])) {");
  return [
    `<meta name="theme-color" content="${light}" media="(prefers-color-scheme: light)" />`,
    `<meta name="theme-color" content="${dark}" media="(prefers-color-scheme: dark)" />`,
  ];
}

function readShell(): Shell {
  const file = join(DIST, 'index.html');
  if (!existsSync(file)) fail('dist/index.html is missing: run `vite build` first');
  const html = readFileSync(file, 'utf8');
  const take = (re: RegExp, what: string): string => {
    const m = re.exec(html);
    if (!m) fail(`dist/index.html has no ${what}`);
    return m[0].replace(/\s+/g, ' ');
  };
  const icons = [...html.matchAll(/<link rel="(?:icon|apple-touch-icon|manifest)"[^>]*>/g)].map((m) => m[0].replace(/\s+/g, ' '));
  for (const rel of ['icon', 'apple-touch-icon', 'manifest']) {
    if (!icons.some((l) => l.startsWith(`<link rel="${rel}"`))) fail(`dist/index.html has no ${rel} link`);
  }
  return {
    html,
    icons,
    fonts: [
      take(/<link rel="preconnect" href="https:\/\/fonts\.googleapis\.com"[^>]*>/, 'Google Fonts preconnect'),
      take(/<link rel="preconnect" href="https:\/\/fonts\.gstatic\.com"[^>]*>/, 'Google Fonts static preconnect'),
      take(/<link\s+href="https:\/\/fonts\.googleapis\.com\/css2[^"]*"\s+rel="stylesheet"\s*\/>/, 'Google Fonts stylesheet'),
    ],
  };
}

/** The Open Graph picture: one 1200 x 630 file for every page, at an absolute address. */
function imageTags(): string[] {
  return [
    `<meta property="og:image" content="${esc(absoluteUrl(SITE_URL, OG_IMAGE.path))}" />`,
    `<meta property="og:image:width" content="${OG_IMAGE.width}" />`,
    `<meta property="og:image:height" content="${OG_IMAGE.height}" />`,
    `<meta property="og:image:alt" content="${esc(OG_IMAGE.alt)}" />`,
  ];
}

interface SharedTags {
  title: string;
  description: string;
  canonical: string;
  graph: object[];
}

/** Canonical, Open Graph, Twitter card and structured data: the tags a page and an app shell have in common. */
function sharedTags(t: SharedTags): string[] {
  return [
    `<link rel="canonical" href="${esc(t.canonical)}" />`,
    '<meta property="og:type" content="website" />',
    `<meta property="og:site_name" content="${SITE_NAME}" />`,
    `<meta property="og:title" content="${esc(t.title)}" />`,
    `<meta property="og:description" content="${esc(t.description)}" />`,
    `<meta property="og:url" content="${esc(t.canonical)}" />`,
    ...imageTags(),
    '<meta name="twitter:card" content="summary_large_image" />',
    `<meta name="twitter:title" content="${esc(t.title)}" />`,
    `<meta name="twitter:description" content="${esc(t.description)}" />`,
    `<meta name="twitter:image" content="${esc(absoluteUrl(SITE_URL, OG_IMAGE.path))}" />`,
    `<meta name="twitter:image:alt" content="${esc(OG_IMAGE.alt)}" />`,
    jsonLd(t.graph),
  ];
}

/** The whole head of a static page, with canonical, share tags and data; or, for the 404 page, a noindex and nothing to share. */
function staticHead(shell: Shell, t: { title: string; description: string; share?: Pick<SharedTags, 'canonical' | 'graph'> }): string {
  return [
    '<meta charset="UTF-8" />',
    '<meta name="viewport" content="width=device-width, initial-scale=1.0" />',
    '<meta name="color-scheme" content="light dark" />',
    `<title>${esc(t.title)}</title>`,
    `<meta name="description" content="${esc(t.description)}" />`,
    // A 404 page is not a page of the site: no canonical, no share tags, and engines are told to leave it out.
    ...(t.share ? sharedTags({ title: t.title, description: t.description, ...t.share }) : ['<meta name="robots" content="noindex" />']),
    ...themeColours(),
    ...shell.icons,
    ...shell.fonts,
    '<link rel="stylesheet" href="/site/site.css" />',
  ]
    .map((line) => `    ${line}`)
    .join('\n');
}

function htmlDocument(head: string, body: string): string {
  return `<!doctype html>\n<html lang="en-IN" data-look="quiet">\n  <head>\n${head}\n  </head>\n  <body>\n${body}\n  </body>\n</html>\n`;
}

/* ----------------------------------------------------------------------------
 * The two app shells (Home and Pricing)
 * ------------------------------------------------------------------------- */

function between(html: string, begin: string, end: string): string {
  const from = html.indexOf(begin);
  const to = html.indexOf(end);
  if (from === -1 || to === -1 || to < from) return html;
  return html.slice(0, from).replace(/[ \t]*$/, '') + html.slice(to + end.length).replace(/^\n/, '');
}

/** What the shell's no-script body links to besides the seed: the site's pages, and the company's own. */
function noscriptLinks(path: string): string {
  const pages = SITE_PAGES.filter((p) => p.path !== path)
    .map((p) => `<li><a href="${p.path}">${esc(p.label)}</a></li>`)
    .join('');
  const company = COMPANY_LINKS.map((l) => `<li><a href="${esc(l.href)}">${esc(l.label)}</a></li>`).join('');
  return `<p>The app needs JavaScript. These pages work without it:</p><ul>${pages}${company}</ul>`;
}

/**
 * The shell with a new title, description, share tags and structured data, and, inside <noscript>, the seed text of the page
 * (what a crawler without JavaScript reads) and a list of the site's pages. The seed stays out of #root on purpose: the
 * same index.html opens every route of the app, and nothing may flash there before the app mounts.
 */
function applyAppHead(shellHtml: string, head: AppHead, path: string, graph: object[], seed: ReactElement): string {
  let html = between(between(shellHtml, SEO_BEGIN, SEO_END), NOSCRIPT_BEGIN, NOSCRIPT_END);
  const title = `<title>${esc(head.title)}</title>`;
  const description = `<meta name="description" content="${esc(head.description)}" />`;
  if (!/<title>[\s\S]*?<\/title>/.test(html)) fail('the app shell has no <title>');
  if (!/<meta name="description"[^>]*>/.test(html)) fail('the app shell has no meta description');
  const tags = [...themeColours(), ...sharedTags({ title: head.title, description: head.description, canonical: absoluteUrl(SITE_URL, path), graph })];
  html = html.replace(/<title>[\s\S]*?<\/title>/, () => title);
  html = html.replace(
    /<meta name="description"[^>]*>/,
    () => `${description}\n    ${SEO_BEGIN}\n${tags.map((t) => `    ${t}`).join('\n')}\n    ${SEO_END}`,
  );
  const noscript = `${NOSCRIPT_BEGIN}<noscript>${renderToStaticMarkup(seed)}${noscriptLinks(path)}</noscript>${NOSCRIPT_END}`;
  const root = '<div id="root"></div>';
  if (!html.includes(root)) fail('the app shell has no <div id="root">');
  return html.replace(root, () => `${root}\n    ${noscript}`);
}

/**
 * The shell for the app's private routes (match, discover, practice, a published portfolio...): no canonical, no structured
 * data, no share tags, and `noindex`. The server answers those routes with it instead of the Home shell, which names
 * Home as the canonical of every URL it opens.
 */
function privateShell(shellHtml: string): string {
  let html = between(between(shellHtml, SEO_BEGIN, SEO_END), NOSCRIPT_BEGIN, NOSCRIPT_END);
  if (!/<meta name="description"[^>]*>/.test(html)) fail('the app shell has no meta description');
  const tags = ['<meta name="robots" content="noindex" />', ...themeColours()];
  html = html.replace(/<meta name="description"[^>]*>/, (m) => `${m}\n    ${SEO_BEGIN}\n${tags.map((t) => `    ${t}`).join('\n')}\n    ${SEO_END}`);
  return html;
}

/* ----------------------------------------------------------------------------
 * Stylesheet, sitemap, robots
 * ------------------------------------------------------------------------- */

/** tokens.css, global.css and site.css as one file without comments: the pages load nothing else but the fonts. */
function bundleCss(): string {
  const parts = ['src/theme/tokens.css', 'src/styles/global.css', 'src/site/site.css'].map((f) => readFileSync(resolve(ROOT, f), 'utf8'));
  const css = parts
    .join('\n')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/[ \t]+$/gm, '')
    .replace(/\n{2,}/g, '\n')
    .trim();
  return `${css}\n`;
}

/** Where each page's own text lives, so its last change can be read from git. Home and Pricing are the app's screens: their seed and the screen. */
const PAGE_FILES: Record<string, string[]> = {
  '/': ['src/site/seeds/HomeSeed.tsx', 'src/views/Onboarding.tsx'],
  '/pricing': ['src/site/seeds/PricingSeed.tsx', 'src/billing/PricingView.tsx'],
  '/how-it-works': ['src/site/pages/HowItWorks.tsx'],
  '/skill-graph': ['src/site/pages/SkillGraph.tsx'],
  '/job-match': ['src/site/pages/JobMatch.tsx'],
  '/mock-interview': ['src/site/pages/MockInterview.tsx'],
  '/job-search': ['src/site/pages/JobSearch.tsx'],
  '/career-plan': ['src/site/pages/CareerPlan.tsx'],
  '/resume-to-portfolio': ['src/site/pages/ResumeToPortfolio.tsx'],
  '/about': ['src/site/pages/About.tsx'],
};

const ymd = (d: Date): string => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

/**
 * The day the text of a page last changed: the date of the last commit that touched its files, or, when one of them has
 * uncommitted changes (or is not tracked yet), the day that file was last written. undefined when it cannot be known
 * (no git, no such file): a date is never made up, and a page without one says nothing.
 */
function lastModified(path: string): string | undefined {
  const files = (PAGE_FILES[path] ?? []).filter((f) => existsSync(resolve(ROOT, f)));
  if (files.length === 0) return undefined;
  try {
    const git = (args: string[]): string => execFileSync('git', args, { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    const dirty = git(['status', '--porcelain', '--', ...files]).split('\n').filter(Boolean);
    if (dirty.length > 0) {
      const newest = Math.max(...files.map((f) => statSync(resolve(ROOT, f)).mtimeMs));
      return ymd(new Date(newest));
    }
    const day = git(['log', '-1', '--format=%cs', '--', ...files]);
    return /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : undefined;
  } catch {
    return undefined;
  }
}

function sitemap(): string {
  const urls = SITE_PAGES.map((p) => {
    const modified = lastModified(p.path);
    return `  <url><loc>${esc(absoluteUrl(SITE_URL, p.path))}</loc>${modified ? `<lastmod>${modified}</lastmod>` : ''}</url>`;
  }).join('\n');
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`;
}

/** Crawlers of AI search and answer engines, named so that the rule for them is on record. Allowed, with the same two Disallows. */
const AI_CRAWLERS = [
  'GPTBot',
  'OAI-SearchBot',
  'ChatGPT-User',
  'ClaudeBot',
  'Claude-SearchBot',
  'Claude-User',
  'PerplexityBot',
  'Google-Extended',
  'Applebot-Extended',
] as const;

function robots(): string {
  // /p/ (share links) is not disallowed on purpose: a crawler must be able to fetch a page to see its noindex header.
  const rules = ['Allow: /', 'Disallow: /api/', 'Disallow: /dev/'];
  return [
    'User-agent: *',
    ...rules,
    '',
    ...AI_CRAWLERS.map((bot) => `User-agent: ${bot}`),
    ...rules,
    '',
    `Sitemap: ${SITE_URL}/sitemap.xml`,
    '',
  ].join('\n');
}

/** llms.txt: what Leafout is, who it is for and where the public pages are, in the format language-model fetchers read. No prices: they are server settings. */
function llmsTxt(): string {
  const blurbs: Record<string, string> = {
    '/': HOME_HEAD.description,
    '/pricing': 'What is free, what the pack of three postings and the 30-day pass include, and how payment, renewal and refunds work.',
  };
  for (const page of CONTENT_PAGES) blurbs[page.path] = page.blurb;
  const pages = SITE_PAGES.map((p) => `- [${p.label}](${absoluteUrl(SITE_URL, p.path)}): ${blurbs[p.path] ?? ''}`.trimEnd());
  const company: Record<string, string> = {
    'Leafout website': 'The company site.',
    Blog: 'Guides on reading job postings and showing evidence of your skills.',
    Privacy: 'The privacy policy.',
    Terms: 'The terms of use.',
    Refunds: 'The refund, cancellation and delivery policy.',
    Contact: 'How to reach Leafout: aman@leafout.in.',
  };
  return [
    `# ${SITE_NAME}`,
    '',
    `> ${SITE_LINE} It checks job postings against that evidence and builds a resume and cover note only from facts you can cite. It gives no ATS score and never scrapes LinkedIn or Naukri.`,
    '',
    'Leafout is for students in India and working professionals of every profession, in India and elsewhere. The skill graph, next roles and one posting match each month are free; paid plans are one-time payments (a pack of three postings, a 30-day pass), and nothing renews. This is the app; the company site is ' +
      `${COMPANY_URL}.`,
    '',
    '## Pages',
    ...pages,
    '',
    '## Company',
    ...COMPANY_LINKS.map((l) => `- [${l.label}](${l.href}): ${company[l.label] ?? ''}`.trimEnd()),
    '',
    '## What Leafout refuses',
    '- No claim without a cited fact, and no invented numbers.',
    '- No percentage ATS match and no per-vendor pass or fail.',
    '- No scraping of LinkedIn, Naukri or any board that forbids automated reading.',
    '',
  ].join('\n');
}

/* ----------------------------------------------------------------------------
 * Main
 * ------------------------------------------------------------------------- */

/** A page's markup inside the site frame (header, footer, ambient field). */
const frame = (currentPath: string, page: ReactElement): string =>
  renderToStaticMarkup(createElement(Layout, { currentPath, children: page }));

const wordCount = (html: string): number =>
  html
    .replace(/<[^>]*>/g, ' ')
    .split(/\s+/)
    .filter(Boolean).length;

function main(): void {
  validateContent();
  const shell = readShell();
  const rows: string[] = [];

  write('site/site.css', bundleCss());

  for (const page of CONTENT_PAGES) {
    const updated = lastModified(page.path);
    const body = frame(page.path, createElement(Article, { page, updated }));
    const head = staticHead(shell, {
      title: documentTitle(page),
      description: page.description,
      share: { canonical: absoluteUrl(SITE_URL, page.path), graph: pageLd(SITE_URL, page, updated) },
    });
    write(`${page.path.slice(1)}/index.html`, htmlDocument(head, body));
    const main = /<main[\s\S]*<\/main>/.exec(body)?.[0] ?? body;
    rows.push(`  ${page.path.padEnd(22)} ${String(wordCount(main)).padStart(4)} words  ${documentTitle(page)}`);
  }

  const notFoundHead = staticHead(shell, {
    title: `That page does not exist · ${SITE_NAME}`,
    description: 'The page you asked for is not here. These are the pages of the site.',
  });
  write('404.html', htmlDocument(notFoundHead, frame('', createElement(NotFound))));

  const base = [organizationLd(), websiteLd(SITE_URL), softwareLd(SITE_URL)];
  write('index.html', applyAppHead(shell.html, HOME_HEAD, '/', base, createElement(HomeSeed)));
  write(
    'pricing/index.html',
    applyAppHead(shell.html, PRICING_HEAD, '/pricing', [...base, breadcrumbLd(SITE_URL, '/pricing'), faqLd(PRICING_FAQ)], createElement(PricingSeed)),
  );
  write('app-shell.html', privateShell(shell.html));
  write('sitemap.xml', sitemap());
  write('robots.txt', robots());
  write('llms.txt', llmsTxt());

  console.log(
    `build-pages: ${CONTENT_PAGES.length} pages, 404.html, the Home, Pricing and private shells, sitemap.xml, robots.txt, llms.txt, site/site.css  (${SITE_URL})`,
  );
  console.log(rows.join('\n'));
}

main();
