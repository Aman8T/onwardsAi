/*
 * Build pages — writes the crawlable part of the site into dist/ after `vite build`.
 *
 *   dist/<slug>/index.html   every `page` of src/site/links.ts, as full static HTML (no app script)
 *   dist/404.html            the same frame, "That page does not exist", links to every page
 *   dist/index.html          the app shell for Home, its head rewritten (title, description, canonical, share tags, data)
 *   dist/pricing/index.html  the app shell again with the pricing head; the app's own script and stylesheet stay in place
 *   dist/site/site.css       tokens.css + global.css + site.css in one file, not content-hashed
 *   dist/sitemap.xml, dist/robots.txt
 *
 * The address used in canonical links, share tags and the sitemap is SITE_URL when set, else DEFAULT_SITE_URL.
 * Re-running is safe: what this script added to the two app shells is marked and replaced, not stacked.
 * Run it with `tsx scripts/build-pages.tsx` (npm run build / build:static / check:build do). The JSX of the site
 * modules is compiled by tsx under the project's tsconfig; this file uses createElement because scripts/ is outside it.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { createElement } from 'react';
import type { ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { Article } from '../src/site/Article';
import { Layout } from '../src/site/Layout';
import { NotFound } from '../src/site/NotFound';
import { SITE_PAGES } from '../src/site/links';
import { CONTENT_PAGES } from '../src/site/pages';
import {
  HOME_HEAD,
  MAX_DESCRIPTION,
  PRICING_HEAD,
  SITE_LINE,
  SITE_NAME,
  absoluteUrl,
  breadcrumbLd,
  documentTitle,
  organizationLd,
  pageLd,
  resolveSiteUrl,
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
}

/* ----------------------------------------------------------------------------
 * The head
 * ------------------------------------------------------------------------- */

interface Shell {
  html: string;
  icon: string;
  fonts: string[];
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
  return {
    html,
    icon: take(/<link rel="icon"[^>]*>/, 'favicon link'),
    fonts: [
      take(/<link rel="preconnect" href="https:\/\/fonts\.googleapis\.com"[^>]*>/, 'Google Fonts preconnect'),
      take(/<link rel="preconnect" href="https:\/\/fonts\.gstatic\.com"[^>]*>/, 'Google Fonts static preconnect'),
      take(/<link\s+href="https:\/\/fonts\.googleapis\.com\/css2[^"]*"\s+rel="stylesheet"\s*\/>/, 'Google Fonts stylesheet'),
    ],
  };
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
    '<meta name="twitter:card" content="summary" />',
    `<meta name="twitter:title" content="${esc(t.title)}" />`,
    `<meta name="twitter:description" content="${esc(t.description)}" />`,
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
    shell.icon,
    ...shell.fonts,
    '<link rel="stylesheet" href="/site/site.css" />',
  ]
    .map((line) => `    ${line}`)
    .join('\n');
}

function htmlDocument(head: string, body: string): string {
  return `<!doctype html>\n<html lang="en">\n  <head>\n${head}\n  </head>\n  <body>\n${body}\n  </body>\n</html>\n`;
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

/** The shell with a new title, description, share tags and structured data, and a no-script list of the site's pages. */
function applyAppHead(shellHtml: string, head: AppHead, path: string, graph: object[]): string {
  let html = between(between(shellHtml, SEO_BEGIN, SEO_END), NOSCRIPT_BEGIN, NOSCRIPT_END);
  const title = `<title>${esc(head.title)}</title>`;
  const description = `<meta name="description" content="${esc(head.description)}" />`;
  if (!/<title>[\s\S]*?<\/title>/.test(html)) fail('the app shell has no <title>');
  if (!/<meta name="description"[^>]*>/.test(html)) fail('the app shell has no meta description');
  const tags = sharedTags({ title: head.title, description: head.description, canonical: absoluteUrl(SITE_URL, path), graph });
  html = html.replace(/<title>[\s\S]*?<\/title>/, () => title);
  html = html.replace(
    /<meta name="description"[^>]*>/,
    () => `${description}\n    ${SEO_BEGIN}\n${tags.map((t) => `    ${t}`).join('\n')}\n    ${SEO_END}`,
  );

  const links = SITE_PAGES.filter((p) => p.path !== path)
    .map((p) => `<li><a href="${p.path}">${esc(p.label)}</a></li>`)
    .join('');
  const noscript =
    `${NOSCRIPT_BEGIN}<noscript><h1>${SITE_NAME}</h1><p>${esc(SITE_LINE)}</p>` +
    `<p>The app needs JavaScript. These pages work without it:</p><ul>${links}</ul></noscript>${NOSCRIPT_END}`;
  const root = '<div id="root"></div>';
  if (!html.includes(root)) fail('the app shell has no <div id="root">');
  return html.replace(root, () => `${root}\n    ${noscript}`);
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

function sitemap(): string {
  const urls = SITE_PAGES.map((p) => `  <url><loc>${esc(absoluteUrl(SITE_URL, p.path))}</loc></url>`).join('\n');
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`;
}

function robots(): string {
  // /p/ (share links) is not disallowed on purpose: a crawler must be able to fetch a page to see its noindex header.
  return ['User-agent: *', 'Allow: /', 'Disallow: /api/', 'Disallow: /dev/', '', `Sitemap: ${SITE_URL}/sitemap.xml`, ''].join('\n');
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
    const body = frame(page.path, createElement(Article, { page }));
    const head = staticHead(shell, {
      title: documentTitle(page),
      description: page.description,
      share: { canonical: absoluteUrl(SITE_URL, page.path), graph: pageLd(SITE_URL, page) },
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

  const base = [organizationLd(SITE_URL), websiteLd(SITE_URL)];
  write('index.html', applyAppHead(shell.html, HOME_HEAD, '/', base));
  write('pricing/index.html', applyAppHead(shell.html, PRICING_HEAD, '/pricing', [...base, breadcrumbLd(SITE_URL, '/pricing')]));
  write('sitemap.xml', sitemap());
  write('robots.txt', robots());

  console.log(`build-pages: ${CONTENT_PAGES.length} pages, 404.html, the Home and Pricing shells, sitemap.xml, robots.txt, site/site.css  (${SITE_URL})`);
  console.log(rows.join('\n'));
}

main();
